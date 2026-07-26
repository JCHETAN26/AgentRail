# Architecture overview

Scope: the system as it exists after Phase 0. Components scheduled for later phases are named where
a current decision was made to accommodate them, and are marked as not built.

## Services

| Service                     | Runtime               | Port               | Responsibility                                                   |
| --------------------------- | --------------------- | ------------------ | ---------------------------------------------------------------- |
| `apps/web`                  | Next.js 15 / React 19 | 3000               | Developer console. No business logic in components.              |
| `services/api`              | FastAPI (Python 3.12) | 8000               | The only writer of new jobs. Owns the schema and its migrations. |
| `services/worker`           | Python 3.12           | 8200 (health only) | The only executor of jobs. Consumes from Redis.                  |
| `services/cloudops-sandbox` | FastAPI               | 8100               | Synthetic, deterministic tool surface. Holds no state.           |

Shared code lives in `packages/core-py` (settings, logging, correlation, infrastructure clients and
the job table) and `packages/contracts` (OpenAPI snapshot and the TypeScript types generated from
it).

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
| Tool surface         | `services/cloudops-sandbox`                          | Clocks, randomness, I/O — it must stay deterministic          |

## State machine

```text
PENDING ──▶ RUNNING ──▶ COMPLETED
   │            │
   └────────────┴──────▶ FAILED
```

`COMPLETED` and `FAILED` have **no** outgoing transitions, including to themselves. That is what
makes a duplicated or delayed queue message a no-op rather than a second execution. The transition
table is enforced twice: by the domain guard before the write, and by the `WHERE state = <expected>`
clause of the write itself, which is what actually resolves a race between two workers.

## Data stores

- **PostgreSQL** — authoritative for all job state. A server-side `statement_timeout` is set on every
  connection so a pathological query cannot pin a worker or an API request.
- **Redis** — task delivery only, plus (later) leases, short-lived rate limits and ephemeral cache.
  Never authoritative.
- **MinIO** — S3-compatible object storage. Provisioned in Compose for dataset and report storage in
  Phase 4; not yet used by any service.

## Observability

Every request is assigned a `CorrelationContext` (`correlation_id`, `trace_id`, `span_id`) by
`CorrelationMiddleware`, bound to a `contextvar` for the duration of the request, echoed in response
headers, forwarded on outbound calls, and stored on the job row so the whole path can be recovered
after the fact. Logs are single-line JSON with automatic redaction of sensitive keys.

Spans are **not** exported yet. The OpenTelemetry SDK and Collector pipeline are Phase 13; the
identifier plumbing exists now so that work is an addition rather than a retrofit. See
[ADR 0004](../adr/0004-correlation-identifiers-before-opentelemetry.md).

## Not built yet

Authentication and tenancy (Phase 1), the full CloudOps sandbox (Phase 2), agent registry (Phase 3),
datasets and suites (Phase 4), durable distributed execution with a transactional outbox and leases
(Phase 5), trajectories (Phase 6), evaluators (Phase 7), replay (Phase 8), failure injection
(Phase 9), policy and approvals (Phase 10), release gates and GitHub Checks (Phase 11), canary and
rollback (Phase 12).
