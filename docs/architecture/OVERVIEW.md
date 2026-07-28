# Architecture overview

Scope: the system as it exists during Phase 5. Components scheduled for later phases are named where
a current decision was made to accommodate them, and are marked as not built.

## Services

| Service                     | Runtime               | Port               | Responsibility                                                    |
| --------------------------- | --------------------- | ------------------ | ----------------------------------------------------------------- |
| `apps/web`                  | Next.js 15 / React 19 | 3000               | Developer console. No business logic in components.               |
| `services/api`              | FastAPI (Python 3.12) | 8000               | The writer of jobs, evaluation runs and migrations.               |
| `services/worker`           | Python 3.12           | 8200 (health only) | The executor of jobs and evaluation runs. Consumes from Redis.    |
| `services/cloudops-sandbox` | FastAPI               | 8100               | Synthetic, deterministic tool surface with in-process test state. |

Shared code lives in `packages/core-py` (settings, logging, correlation, infrastructure clients,
state machines and persistence models) and `packages/contracts` (OpenAPI snapshot and the TypeScript
types generated from it).

## Request path

```text
 browser
    │  POST /api/v1/jobs                 x-correlation-id, traceparent
    ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ Platform API                                                 │
 │  CorrelationMiddleware  →  MaxBodySizeMiddleware  →  route    │
 │  1. INSERT job (state = PENDING)      ── committed first ──┐  │
 │  2. RPUSH job id to Redis             ── only after (1) ───┘  │
 └──────────────────────────────────────────────────────────────┘
    │                                        │
    │ 201 + job id                           │ job id
    ▼                                        ▼
 browser polls GET /api/v1/jobs/{id}      ┌─────────────────────────────┐
                                          │ Worker                       │
                                          │  BLPOP                       │
                                          │  UPDATE … WHERE state=PENDING│  ← the claim
                                          │  POST /v1/tasks/noop ────────┼──▶ sandbox
                                          │  UPDATE … WHERE state=RUNNING│  ← the completion
                                          └─────────────────────────────┘
```

### Why the commit precedes the publish

If the identifier were published first, a worker could dequeue an id for a row that PostgreSQL has
not committed — or, after a rollback, never will. Committing first means the only failure mode is a
job that exists but was never announced, which the worker's recovery sweep repairs. See
[ADR 0002](../adr/0002-postgresql-authoritative-redis-delivery.md).

## Boundaries

The layers below are kept separate on purpose. A change that blurs one of them should be challenged
in review.

| Layer                | Where                                                | Must not contain                                              |
| -------------------- | ---------------------------------------------------- | ------------------------------------------------------------- |
| HTTP transport       | `services/api/src/agentrail_api/routers/`            | Business rules; routers translate requests into service calls |
| Use cases            | `services/api/src/agentrail_api/jobs/service.py`     | HTTP objects, framework imports                               |
| Domain state machine | `packages/core-py/src/agentrail_core/jobs/state.py`  | SQLAlchemy, FastAPI, Redis                                    |
| Persistence          | `packages/core-py/src/agentrail_core/jobs/models.py` | Decisions about _when_ a transition is legal                  |
| Delivery             | `packages/core-py/src/agentrail_core/queue.py`       | Authoritative state                                           |
| Tool surface         | `services/cloudops-sandbox`                          | Real infrastructure I/O — all output must stay synthetic      |

## CloudOps sandbox

The sandbox exposes the Phase 2 reference workload beside the original no-op task. It includes the
ten CloudOps tool contracts from the build plan, deterministic service health, metric, log,
dependency and runbook reads, idempotent side-effecting tools for restarts, scaling, incidents and
notifications, a human-escalation tool, reset/seed endpoints, fault-injection hooks and 25 scenario
manifests with ground truth. All state is in process and resettable; nothing represents real
production telemetry.

## Agent Registry

Agent definitions are stable logical identities scoped to a project. Agent versions are immutable
snapshots containing graph spec, prompt bundle, model configuration, tool contracts, policy bundle,
optional source commit and a canonical content digest. The API exposes create/list/read operations
only; changing an agent means creating another version.

## Datasets and suites

Datasets are stable project-scoped containers. Dataset versions are immutable records created from
JSONL or CSV uploads after validation. Each version stores a content digest, storage URI, schema
metadata, validation report, item count and partition counts. Evaluation suites bind one dataset
version to evaluator configuration, thresholds and fault profiles, then can be frozen by setting a
single timestamp. Evaluation runs may only be created from frozen suites.

## Durable evaluation execution

An evaluation run expands a frozen suite into one `run_items` row per dataset item. The API validates
tenant scope, suite immutability and agent-version project ownership, then commits the run, items and
an `outbox_events` row in the same PostgreSQL transaction. Redis receives only the run identifier
after commit. If publish fails, the worker recovery loop finds unpublished outbox events and
re-delivers them.

Run items are claimed with row locks, leased to a worker, checkpointed in PostgreSQL and retried until
their budget is exhausted. Duplicate run deliveries are harmless because the worker claims a run with
conditional state updates and skips terminal runs. Cancellation marks the run and all non-terminal
items cancelled; aggregation records item counts and the final run outcome.

The current executor is deterministic/recorded. It proves the durable execution mechanics over frozen
suite cardinality; evaluator execution arrives in later phases.

## Trajectory capture

Each executed run item receives one trajectory header, ordered steps and named checkpoints. The
deterministic executor records input loading, graph state, tool-call, evidence, checkpoint and final
result steps. Payloads are recursively redacted before persistence: sensitive keys such as tokens,
secrets and API keys become `[REDACTED]`, and email addresses are masked.

The API exposes tenant-scoped trace explorer reads for run items, trajectory headers, ordered steps
and checkpoints. A failed item response can carry both a `trajectory_id` and `failing_step_id`, so a
reviewer can jump directly from a failed run item to the exact step that explains it.

## State machine

Jobs keep the original Phase 0 state machine:

```text
PENDING ──▶ RUNNING ──▶ COMPLETED
   │            │
   └────────────┴──────▶ FAILED
```

`COMPLETED` and `FAILED` have **no** outgoing transitions, including to themselves. That is what
makes a duplicated or delayed queue message a no-op rather than a second execution. The transition
table is enforced twice: by the domain guard before the write, and by the `WHERE state = <expected>`
clause of the write itself, which is what actually resolves a race between two workers.

Evaluation runs add a longer lifecycle:

```text
CREATED ─▶ VALIDATING ─▶ QUEUING ─▶ RUNNING ─▶ AGGREGATING ─▶ PASSED
   │           │             │          │             └────────▶ FAILED
   └───────────┴─────────────┴──────────┴──────────────────────▶ CANCELLED
                                      └─────────────────────────▶ ERROR
```

Run items move through `PENDING`, `LEASED`, `EXECUTING`, `EVALUATING` and then either
`COMPLETED`, `FAILED_RETRYABLE`, `FAILED_TERMINAL` or `CANCELLED`.

## Data stores

- **PostgreSQL** — authoritative for job state, evaluation-run state, run-item leases, trajectories,
  checkpoints and outbox events. A server-side `statement_timeout` is set on every connection so a
  pathological query cannot pin a worker or an API request.
- **Redis** — job and run delivery only, plus later short-lived rate limits and ephemeral cache.
  Never authoritative.
- **MinIO** — S3-compatible object storage. Provisioned in Compose for dataset and report storage.
  Phase 4 records deterministic `s3://agentrail-datasets/...` storage URIs; a concrete object client
  remains deployment work.

## Observability

Every request is assigned a `CorrelationContext` (`correlation_id`, `trace_id`, `span_id`) by
`CorrelationMiddleware`, bound to a `contextvar` for the duration of the request, echoed in response
headers, forwarded on outbound calls, and stored on the job row so the whole path can be recovered
after the fact. Logs are single-line JSON with automatic redaction of sensitive keys.

Phase 14 adds `GET /api/v1/evaluation-runs/{run_id}/metrics`, which joins the run, queue, retry,
budget, evaluator, policy, release-gate and canary records into one incident snapshot. The response
includes the run's `correlation_id`, `trace_id`, trace links and SLO verdict, so an operator can
trace a failed release without joining tables by hand.

External span export is not wired yet. The identifier plumbing exists now so that an OpenTelemetry
SDK and Collector can attach to real context rather than a retrofit. See
[ADR 0004](../adr/0004-correlation-identifiers-before-opentelemetry.md).

## Evaluators And Comparison

Phase 7 adds the first reproducible evaluator substrate. Evaluator definitions are normalized and
versioned by digest, terminal run items are scored during aggregation, and comparison reports store
overall, evaluator-level and category-level metrics. Failed or errored items remain in the
denominator, so a comparison cannot look better by dropping execution errors.

## Multi-Agent Safety Tribunal

Phase 8 adds the deterministic backend foundation for the Safety Tribunal. A Tribunal session is
linked one-to-one with an evaluation run and persists a six-role blackboard: Prosecutor, Defender,
Auditor, Economist, Historian and Judge. The first slice is deliberately model-free: it reads
evaluation-run and comparison-report evidence, records findings and arguments, then writes a Judge
verdict of `approved`, `conditional` or `blocked`.

The Auditor has override power in the deterministic rules. Missing comparison evidence or a
non-reproducible comparison report creates a blocker even when the Defender supports approval. This
gives the platform the Tribunal persistence and API contract without introducing nondeterministic
model calls into CI. The console includes a Tribunal inspector that fetches or creates a run's
deterministic verdict and displays role findings, arguments and the blackboard timeline. Evaluation
suites can also opt into automatic deterministic Tribunal creation with
`thresholds.tribunal.enabled = true`; worker aggregation writes the Tribunal id and outcome into the
run summary after building the comparison report. Release policies can make the Tribunal binding
with `require_tribunal_approval = true`; a missing Tribunal verdict returns a retryable gate
conflict, `conditional` or `blocked` blocks the gate, and `approved` satisfies the rule.

The live-mode scaffold uses the same persisted blackboard but routes each role through a
`TribunalModelClient` protocol with immutable role prompt metadata. In CI and demos, the
`RecordedTribunalModelClient` returns deterministic schema-shaped responses while still recording
provider, model, response id, prompt version and usage provenance. Every model response is validated
before it can become a finding, argument or Judge verdict; malformed output fails closed with an
Auditor blocker. The deterministic Tribunal remains a safety floor, so missing or non-reproducible
comparison evidence cannot be overruled by model output.

An OpenAI-compatible Responses API adapter is available when a suite sets
`thresholds.tribunal.model_provider = "openai"`. API and worker processes read
`AGENTRAIL_OPENAI_API_KEY`, optional `AGENTRAIL_OPENAI_BASE_URL` and
`AGENTRAIL_TRIBUNAL_MODEL_TIMEOUT_SECONDS`; if credentials are absent, Tribunal creation returns a
validation error rather than silently falling back to recorded mode. The adapter requests structured
JSON with the role prompt schema and records provider/model/response id/usage provenance in the
existing Tribunal evidence.

## Replay And Time Travel

The replay/time-travel slice adds durable replay records for persisted trajectories. A recorded
replay hashes the
redacted trajectory, ordered steps and optional checkpoint and must reproduce the same digest. A
forked replay starts from the same evidence but incorporates explicit override metadata, so the
stored replay digest diverges deterministically. Replay records always include a safety summary
showing that original side effects were not repeated.

## Canary And Rollback

Phase 13 adds durable deployment records for candidates that have passed a release gate. The canary
slice compares baseline metrics with observed replay or live-workload metrics. Healthy candidates
promote to full traffic; degraded candidates roll back to zero traffic with the metric deltas and
rollback reasons preserved in release history.

## Not built yet

Forkable Tribunal replay, external OpenTelemetry export, remaining security hardening (Phase 15),
real deploy provider integration and production packaging.
