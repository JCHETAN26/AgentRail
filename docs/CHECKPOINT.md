# Checkpoint

Operational handoff between sessions. This file is the first thing to read when resuming work.

---

## Current state

|                |                                                                                     |
| -------------- | ----------------------------------------------------------------------------------- |
| **Phase**      | 14 — Security and supply chain                                                      |
| **Status**     | In progress on branch `feat/p14-security-supply-chain`                              |
| **Base**       | `main` @ `8ccedca` (Phase 13 merged)                                                |
| **Next phase** | 15 — Performance and analytical scale, after Phase 14 exits                         |
| **Guardrails** | Branch protection live on `main`; direct pushes rejected; 10 required status checks |

Phase 0 shipped in PR [#1](https://github.com/JCHETAN26/AgentRail/pull/1); housekeeping (MIT licence,
applied branch protection, Dependabot triage) in [#19](https://github.com/JCHETAN26/AgentRail/pull/19).
Phase 1 shipped in PR [#20](https://github.com/JCHETAN26/AgentRail/pull/20).
Phase 2 shipped in PR [#21](https://github.com/JCHETAN26/AgentRail/pull/21).
Phase 3 shipped in PR [#22](https://github.com/JCHETAN26/AgentRail/pull/22).
Phase 4 shipped in PR [#23](https://github.com/JCHETAN26/AgentRail/pull/23).
Phase 5 shipped in PR [#24](https://github.com/JCHETAN26/AgentRail/pull/24).
Phase 6 shipped in PR [#25](https://github.com/JCHETAN26/AgentRail/pull/25).
Phase 7 shipped in PR [#26](https://github.com/JCHETAN26/AgentRail/pull/26).
Phase 8 shipped in PR [#41](https://github.com/JCHETAN26/AgentRail/pull/41).
Phase 9 shipped in PR [#42](https://github.com/JCHETAN26/AgentRail/pull/42).
Phase 10 shipped in PR [#43](https://github.com/JCHETAN26/AgentRail/pull/43), with its console in
[#44](https://github.com/JCHETAN26/AgentRail/pull/44).
Phase 11 shipped in PR [#45](https://github.com/JCHETAN26/AgentRail/pull/45).
Phase 12 shipped in PR [#46](https://github.com/JCHETAN26/AgentRail/pull/46).
Phase 13 shipped in PR [#47](https://github.com/JCHETAN26/AgentRail/pull/47).

Merged branches through Phase 9 are deleted, local and remote. The repository does not
auto-delete on merge, so each phase has to clean up after itself.

---

## Read these first

1. `BUILDPLAN.md` Phase 14 — the exit criterion: cross-tenant/security workflows green
2. `services/api/src/agentrail_api/security.py` — Redis-backed rate and replay guards
3. `services/api/src/agentrail_api/dependencies.py` — authenticated caller rate-limit wiring
4. `services/api/src/agentrail_api/routers/integrations.py` — GitHub webhook replay defence
5. `.github/workflows/ci.yml` — `containers / scan`
6. `docs/security/THREAT_MODEL.md`

---

## Completed capabilities (through Phase 7)

- **Delegated authentication** with two providers behind one protocol: a deterministic dev provider
  (no credentials, no network — used by local development, CI and the demo) and GitHub OAuth with
  `state` verification for deployed environments.
- **Opaque server-side sessions**: 256-bit tokens stored only as one-way digests, `HttpOnly`,
  `SameSite=Lax`, `Secure` when deployed, revoked on sign-out.
- **Organisations, memberships and projects**, with five roles (owner, admin, developer, reviewer,
  viewer) that form a strict capability ladder.
- **Scoped API keys** — `ar_<key_id>_<secret>`, hashed, constant-time compared, bounded by both a
  role and optional scopes, revocable, optionally expiring, returned exactly once.
- **One central authorisation function**, pure and exhaustively unit-tested, used by every route.
- **Tenant scoping on every query**, with jobs moved under `/api/v1/projects/{project_id}/jobs` and
  `list_jobs` requiring `project_id` as a keyword argument.
- **Audit foundation**: append-only events with redacted context and the request's correlation id.
- **Console**: sign-in, tenant context, first-organisation onboarding, sign-out, and complete
  signed-out / loading / empty / permission-denied states.

---

## Phase 2 progress

- Added the ten CloudOps tool contracts:
  `get_service_health`, `query_metrics`, `search_logs`, `get_dependency_graph`, `get_runbook`,
  `restart_service`, `scale_service`, `create_incident`, `notify_oncall` and
  `escalate_to_human`.
- Added risk, side-effect class, approval requirement and idempotency-key metadata for each tool.
- Added deterministic synthetic service health, metrics, logs, dependency graphs and runbooks.
- Added in-process idempotency records for side-effecting tools. Duplicate keys return the original
  result with `idempotent_replay = true`.
- Added reset and seed endpoints.
- Added fault hooks for latency, timeout, HTTP 500, malformed, stale, rate limit and unavailable.
- Added 25 deterministic scenario manifests covering the 16 build-plan incident families, with
  ground truth for diagnosis, allowed/forbidden tools, expected arguments, remediation/approval,
  evidence and budgets.

## Phase 3 progress

- Added project-scoped `AgentDefinition` and immutable `AgentVersion` persistence models.
- Added Alembic revision `0003_agent_registry`.
- Added `agent:read` and `agent:manage` permissions.
- Added APIs to create/list project agents, create/list immutable agent versions and fetch a version
  by ID.
- Agent versions include graph spec, prompt bundle, model configuration, tool contracts, policy
  bundle, optional source commit and canonical SHA-256 content digest.
- Duplicate version content for the same agent is rejected.
- Bare agent/version ID routes resolve tenant scope through the owning project before returning data.

## Phase 4 progress

- Added project-scoped `Dataset`, immutable `DatasetVersion` and `EvaluationSuite` persistence
  models.
- Added Alembic revision `0004_datasets_suites`.
- Added `dataset:read` and `dataset:manage` permissions.
- Added APIs to create/list project datasets, create immutable dataset versions, fetch validation
  reports, create evaluation suites and freeze suites.
- Dataset versions validate JSONL and CSV input, reject malformed records with line/record details,
  and store content digest, storage URI, record schema, validation report, item count and partition
  counts.
- Evaluation suites bind one dataset version to evaluator settings, thresholds, fault profiles and a
  preview summary. Freezing is idempotent and preserves the first `frozen_at` timestamp.

## Phase 5 progress

- Added evaluation-run and run-item state machines with terminal-state guards.
- Added `EvaluationRun`, `RunItem` and `OutboxEvent` persistence models.
- Added Alembic revision `0005_durable_execution`.
- Added `run:read`, `run:create` and `run:cancel` permissions.
- Added APIs to create evaluation runs from frozen suites, fetch runs, cancel runs and stream
  progress snapshots over SSE.
- Run creation validates tenant scope, frozen-suite status and agent-version project ownership, then
  creates the run, items and outbox event in one transaction.
- Worker now consumes both the legacy job queue and the evaluation-run queue, claims runs with
  conditional updates, leases run items, checkpoints progress, recovers expired leases and aggregates
  final run outcomes.

## Phase 6 progress

- Added `Trajectory`, `TrajectoryStep` and `TrajectoryCheckpoint` persistence models.
- Added Alembic revision `0006_trajectories`.
- Added recursive trajectory redaction for sensitive keys and email addresses before persistence.
- Worker now records deterministic input, graph-state, tool-call, evidence, checkpoint and
  final-result steps for each completed run item.
- Added trace explorer APIs to list run items with trajectory/failing-step links, fetch trajectories,
  list ordered steps and list checkpoints.
- Added tenant-isolation and redaction tests for trajectory reads.

## Phase 7 progress

- Added `EvaluatorVersion`, `EvaluationResult` and `ComparisonReport` persistence models.
- Added Alembic revision `0007_evaluators_comparison`.
- Added deterministic programmatic scoring helpers and aggregation metrics.
- Worker now builds comparison reports from terminal run items during run aggregation.
- Errors remain in comparison denominators and are surfaced as regressions.
- Added comparison APIs to fetch a run report and list evaluator results with optional evaluator
  filtering.
- Added aggregation and tenant-isolation tests for comparison reads.

## Phase 8 progress

- Added `TrajectoryReplay` persistence model.
- Added Alembic revision `0008_trajectory_replays`.
- Added recorded, live-labelled and forked replay creation from persisted trajectories and optional
  checkpoints.
- Recorded replays reproduce deterministic trajectory digests; forked replays store deterministic
  divergence metadata from explicit overrides.
- Replay records persist safety summaries proving original side effects were not repeated.
- Added replay create/list APIs under `/api/v1/trajectories/{trajectory_id}/replays`.
- Added audit events and tenant-isolation tests for replay creation.
- Review fixes: replay creation requires `run:create`; fork digests are taken over raw (not redacted)
  overrides so sensitive-keyed forks cannot collide; recorded replays reject `fork_overrides` with
  `validation_failed`; `safety_summary` reports the replay that actually ran rather than the mode
  requested.

## Phase 9 progress

- Added `agentrail_core.faults`: 23 fault kinds across the model, tool and platform families, a
  validated `FaultProfile`, and a pure `plan_fault` selector keyed on item index and attempt.
- Added `agentrail_core.reliability`: budget ledger (tool calls, tokens, loop iterations, latency,
  cost) and a pure circuit breaker with closed/open/half-open transitions.
- Added `agentrail_core.side_effects`: the ledger table, a stable idempotency key and
  `apply_side_effect_once`, which inserts inside a SAVEPOINT so losing the race rolls back only the
  insert.
- Added Alembic revision `0009_failure_injection`, plus `injected_fault` and `budget_state` on run
  items.
- The recorded executor can now fail: it consults the fault plan, charges budgets, applies its one
  side effect through the ledger _before_ any injected fault can kill the attempt, and drives retry
  or terminal transitions accordingly.
- Added the recovery API and the `chaos-duplicate` / `chaos-strand` / `chaos-report` targets.
- Review fixes: budgets resume across retries rather than restarting per attempt; unexecutable fault
  profiles are rejected at suite creation with their index; a legacy unparseable profile fails its
  item with `fault_profile_invalid` instead of propagating out of the worker's consume loop, which
  has no exception handling around `process`.

**Design notes worth keeping.** The side-effect key deliberately excludes the attempt number — a key
that varied per attempt would let every retry insert a fresh row, which is the exact bug the table
exists to prevent. Retryability is a property of the fault, not of the caller: a timeout may succeed
on a second attempt, a refusal will not. And `BudgetExceededError` carries the overrun ledger,
because the caller's own variable is still the pre-charge one and the recovery view would otherwise
report a spend of zero for the charge that broke the budget.

## Phase 10 progress

**Approval console (follow-up branch `feat/p10-approval-console`).** Added the reviewer queue in
`apps/web`, a project-scoped `GET /projects/{project_id}/approvals` behind it, and the
`ix_approval_requests_project_state` index that migration `0010` had already anticipated. This
closes the one part of Phase 10 the build plan asked for that PR #43 did not deliver.

Two things worth carrying forward from it. First, a bare `dict[str, Any]` on a _request_ body emits
`{"type": "object"}` with no `additionalProperties`, which openapi-typescript renders as
`Record<string, never>` — an object permitting no properties, so the generated client cannot send
one. Response-only dictionaries hide this because reading an object never checks the index
signature. `JsonObject` in `approvals/schemas.py` is the fix; any future request field taking free
-form JSON needs it too. Second, a test that stubs `fetch` with `mockResolvedValue(new Response(...))`
shares one body across every call, and a `Response` body can only be read once — so adding any
component that fetches breaks unrelated tests. Use `mockImplementation` returning a fresh response.

- Added `agentrail_core.policy`: the four risk levels, a validated bundle parsed from the agent
  version's existing `policy_bundle` column, and one pure `decide()`.
- Added `agentrail_core.approvals`: `ApprovalRequest`, the approval state machine with no outgoing
  edges from any decided state, and its transition guards.
- Added `AWAITING_APPROVAL` to the run-item machine and rewrote its check constraint in
  `0010_policy_and_approval`.
- Extended the side-effect ledger with `required_approval` and `approval_id` under a `CHECK`, so an
  unapproved high-risk effect is unrepresentable.
- The executor now gates every tool call on policy before anything reaches the world, parks on a
  human without holding a lease or spending a retry, and resumes from the approval — applying the
  reviewer's edit when there is one.
- Added approval APIs, audit events, and the `approval:read` / `approval:decide` permissions.

**Design notes worth keeping.** An unclassified tool defaults to `HIGH_RISK_WRITE`: a policy engine
that fails open is not one. That is a breaking change for agent versions with an empty
`policy_bundle`, which now park instead of running — the existing worker fixtures had to be given an
explicit bundle, which is the right outcome, since a suite that wants unattended execution should
say so. A reviewer's edit changes the arguments and therefore the ledger key, so an edited action is
a _different_ effect and cannot inherit the authorisation recorded for the original. And note the
schema-wide gotcha this phase surfaced: state columns are `String` with a check constraint, so a
loaded row carries a `str`, not the enum its `Mapped[...]` annotation promises — `is` comparisons
and `.value` both fail until you coerce.

## Phase 11 progress

- Added `agentrail_core.release`: the rule kinds, a validated immutable policy, and one pure
  `evaluate_gate` that checks every rule rather than stopping at the first failure.
- Added `agentrail_core.github`: constant-time webhook signature verification and a Check Run
  publisher protocol whose only implementation records rather than delivers.
- Added Alembic revision `0011_release_gates`: immutable versioned policies, gate evaluations unique
  on (run, policy), and nullable pull-request provenance on `evaluation_runs`.
- Added the offline gate API, the webhook receiver, superseded-run cancellation, and a sample
  workflow at `docs/examples/release-gate-workflow.yml`.
- Review fixes: an empty `{}` definition no longer bypasses the "at least one threshold" check;
  unknown rule names are refused rather than silently ignored; pull-request provenance is accepted on
  run creation and persisted, without which cancellation and Check Runs were unreachable; the gate
  reserves its row before publishing so a losing racer cannot speak on a pull request; webhook log
  fields go through an allowlist.
- **Tenant isolation fix worth remembering.** Making provenance client-supplied opened a cross-tenant
  denial of service: cancellation matched repository coordinates with no tenant filter, so any
  project could name another's repository and have the victim's own webhook cancel its runs. Fixed
  with an exclusive `github_repository_bindings` claim that the webhook resolves the project from,
  plus a check that a run may only assert provenance its project holds. The general lesson: a field
  that becomes client-supplied needs its authorisation reconsidered, not just its validation.

**Design notes worth keeping.** A metric a policy names but the report lacks _blocks_ — otherwise
deleting an evaluator silently disables the rule guarding it, which is the exact failure a gate
exists to prevent. The gate is idempotent on (run, policy) under a unique constraint, so a
redelivered webhook cannot produce a second, different verdict; the pre-read is only an
optimisation, and the tests still pass with it removed. And the whole integration is optional by
construction: CI reads the verdict from the response body, so no team needs to install an app or
grant write access to use the gate.

## Phase 12 progress

- Added `agentrail_core.deployments`: deployment states, pure canary decision logic and
  PostgreSQL-backed deployment history.
- Added Alembic revision `0012_canary_deployments`.
- Added deployment APIs:
  `POST /api/v1/deployments`, `POST /api/v1/deployments/{deployment_id}/promote`,
  `POST /api/v1/deployments/{deployment_id}/rollback` and
  `GET /api/v1/projects/{project_id}/deployments`.
- Deployment creation requires a passing release-gate verdict for the run. Missing or blocked gates
  return `409`, so a release cannot skip the gate accidentally.
- Healthy canary metrics promote the candidate to 100% traffic; degraded canary metrics roll back to
  0% traffic and persist the rollback reasons and metric deltas.
- Added pure canary tests and API coverage for promotion, rollback, blocked gates and tenant
  isolation.

## Phase 13 progress

- Added `agentrail_core.observability`: pure SLO evaluation for task success, failed items,
  stranded leases, canary rollbacks and cost.
- Added `GET /api/v1/evaluation-runs/{run_id}/metrics`.
- Metrics snapshots expose the run's correlation ID, trace ID, trace links, queue state,
  retry/lease health, budget spend, quality metrics, approval counts, release-gate status, canary
  rollback data and SLO verdict.
- Added operations docs: `docs/operations/SLO.md` and `docs/operations/INCIDENT_RUNBOOK.md`.
- Added pure SLO tests and API coverage for correlation/trace visibility and tenant isolation.

## Phase 14 progress

- Added Redis-backed fixed-window request limits per authenticated user or API key.
- Added signed GitHub webhook replay defence using `X-GitHub-Delivery` reservations.
- Added `rate_limited` and `replayed_webhook` public error codes.
- Expanded console security headers with CSP and Permissions-Policy.
- Renamed the container CI job to `containers / scan`; it builds each Python service image, checks
  the shared Dockerfile's pinned runtime inputs and proves the runtime user is non-root.

## Architecture decisions taken

| ADR  | Decision                                                                             |
| ---- | ------------------------------------------------------------------------------------ |
| 0001 | Single monorepo; pnpm and uv workspaces; one parameterised Dockerfile                |
| 0002 | PostgreSQL authoritative, Redis delivery-only, commit-before-publish, recovery sweep |
| 0003 | Idempotency keys at the edge, conditional `UPDATE ... WHERE state` in the core       |
| 0004 | Propagate correlation and trace identifiers now; export spans in Phase 13            |
| 0005 | API generates the contract; committed snapshot makes drift a CI failure              |
| 0006 | Delegated auth, opaque sessions, tenancy enforced at one function, 403 never 404     |

---

## Migrations

| Revision                     | Description                                                                                                                                                                      |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0001_create_jobs`           | Creates `jobs` with check constraints and `ix_jobs_state_created_at`                                                                                                             |
| `0002_identity`              | Adds users, organisations, memberships, projects, sessions, api_keys, audit_events; retrofits `jobs.project_id`; moves idempotency uniqueness to `(project_id, idempotency_key)` |
| `0003_agent_registry`        | Adds project-scoped agent definitions and immutable agent versions with per-agent version and digest uniqueness                                                                  |
| `0004_datasets_suites`       | Adds project-scoped datasets, immutable dataset versions with validation metadata and freezable evaluation suites                                                                |
| `0005_durable_execution`     | Adds evaluation runs, run items, retry/lease metadata and durable outbox events                                                                                                  |
| `0006_trajectories`          | Adds trajectory headers, ordered steps, named checkpoints and redacted diagnostic payloads                                                                                       |
| `0007_evaluators_comparison` | Adds evaluator versions, per-item evaluator results and reproducible comparison reports                                                                                          |
| `0008_trajectory_replays`    | Adds durable recorded, live-labelled and forked replay records for trajectories                                                                                                  |
| `0009_failure_injection`     | Adds the side-effect ledger and its unique idempotency key, plus per-item injected-fault and budget state                                                                        |
| `0010_policy_and_approval`   | Adds approval requests, the AWAITING_APPROVAL item state, and the ledger's approval columns under a CHECK constraint                                                             |
| `0011_release_gates`         | Adds immutable release policies, gate evaluations unique on (run, policy), and nullable pull-request provenance on evaluation runs                                               |
| `0012_canary_deployments`    | Adds durable canary deployment, promotion and rollback history                                                                                                                   |

`0002` backfills existing jobs against a synthetic "Legacy" organisation and project, created only if
any jobs exist, using hard-coded identifiers so the migration is deterministic. The downgrade nulls
duplicate idempotency keys before restoring the global unique constraint, since two projects may
legitimately have shared one.

Both migrations were applied, reversed to `base`, and re-applied against real PostgreSQL.

---

## Verification evidence

Run on 2026-07-26 against Docker Compose (PostgreSQL 16.6, Redis 7.4) on macOS 15 / arm64.

| Command                                           | Result                                                                      |
| ------------------------------------------------- | --------------------------------------------------------------------------- |
| `make verify`                                     | Pass — formatting, lint, strict types (47 files), 285 tests, contract drift |
| `uv run pytest -m "not integration"`              | 180 passed                                                                  |
| `uv run pytest -m integration`                    | 72 passed                                                                   |
| `pnpm run test`                                   | 33 passed (23 web, 10 contracts)                                            |
| `alembic upgrade head → downgrade base → upgrade` | Clean                                                                       |

**285 unit + 72 integration tests**, up from 188 + 32 in Phase 0.

The cross-tenant suite provisions two organisations through the public API and asserts that neither
can reach the other's organisation, members, projects, audit log, jobs by identifier, or job listings
— and that a non-existent organisation is byte-identical in response to someone else's.

No benchmark numbers exist and none may be quoted. Benchmarks are Phase 17.

Latest Phase 2 branch checks, run locally on 2026-07-26:

| Command                        | Result                                                |
| ------------------------------ | ----------------------------------------------------- |
| `uv run ruff format --check .` | Pass                                                  |
| `uv run ruff check .`          | Pass                                                  |
| `uv run mypy ...`              | Pass — 48 source files                                |
| `uv run pytest -q`             | 190 passed, 71 skipped locally due sandboxed Postgres |
| `pnpm run format:check`        | Pass                                                  |
| `pnpm run lint`                | Pass                                                  |
| `pnpm run typecheck`           | Pass                                                  |
| `pnpm run test`                | Pass — 34 JS tests                                    |
| `scripts/export_openapi.py`    | Pass — API snapshot unchanged                         |
| `@agentrail/contracts check`   | Pass                                                  |

Latest Phase 3 branch checks, run locally on 2026-07-26:

| Command                      | Result                                                |
| ---------------------------- | ----------------------------------------------------- |
| `uv run ruff format .`       | Pass                                                  |
| `uv run ruff check .`        | Pass                                                  |
| `uv run mypy ...`            | Pass — 53 source files                                |
| `uv run pytest -q`           | 190 passed, 78 skipped locally due sandboxed Postgres |
| `pnpm run format:check`      | Pass                                                  |
| `pnpm run lint`              | Pass                                                  |
| `pnpm run typecheck`         | Pass                                                  |
| `pnpm run test`              | Pass — 34 JS tests                                    |
| `scripts/export_openapi.py`  | Pass                                                  |
| `@agentrail/contracts check` | Pass                                                  |

Latest Phase 4 branch checks, run locally on 2026-07-26:

| Command                                             | Result                                                |
| --------------------------------------------------- | ----------------------------------------------------- |
| `uv run ruff check ...`                             | Pass                                                  |
| `uv run mypy packages/core-py/src services/api/src` | Pass — 46 source files                                |
| `uv run pytest -q`                                  | 194 passed, 87 skipped locally due sandboxed Postgres |
| `@agentrail/contracts check`                        | Pass                                                  |
| `@agentrail/contracts test`                         | Pass — 10 tests                                       |

Latest Phase 5 branch checks, run locally on 2026-07-26:

| Command                                                 | Result                                                |
| ------------------------------------------------------- | ----------------------------------------------------- |
| `uv run ruff check .`                                   | Pass                                                  |
| `uv run mypy packages/core-py/src services/api/src ...` | Pass — 66 source files                                |
| `uv run pytest -q`                                      | 196 passed, 94 skipped locally due sandboxed Postgres |
| `uv run python scripts/export_openapi.py --check`       | Pass                                                  |
| `pnpm run format:check`                                 | Pass                                                  |
| `pnpm run lint`                                         | Pass                                                  |
| `pnpm run typecheck`                                    | Pass                                                  |
| `pnpm run test`                                         | Pass — 34 JS tests                                    |
| `@agentrail/contracts check`                            | Pass                                                  |
| `@agentrail/contracts test`                             | Pass — 10 tests                                       |
| `pnpm build`                                            | Pass — web production build                           |
| `uv build --all-packages`                               | Pass                                                  |

`AGENTRAIL_REQUIRE_INTEGRATION=1` was attempted locally after escalation, but Docker was not running
and PostgreSQL on localhost:5433 refused connections. CI must run the real PostgreSQL/Redis
integration suite before landing.

Latest Phase 6 branch checks, run locally on 2026-07-26:

| Command                                                 | Result                                                |
| ------------------------------------------------------- | ----------------------------------------------------- |
| `uv run ruff check .`                                   | Pass                                                  |
| `uv run mypy packages/core-py/src services/api/src ...` | Pass — 71 source files                                |
| `uv run pytest -q`                                      | 197 passed, 98 skipped locally due sandboxed Postgres |
| `uv run python scripts/export_openapi.py --check`       | Pass                                                  |
| `pnpm run format:check`                                 | Pass                                                  |
| `pnpm run lint`                                         | Pass                                                  |
| `pnpm run typecheck`                                    | Pass                                                  |
| `pnpm run test`                                         | Pass — 34 JS tests                                    |
| `@agentrail/contracts check`                            | Pass                                                  |
| `pnpm build`                                            | Pass — web production build                           |

Latest Phase 7 branch checks, run locally on 2026-07-27:

| Command                                                 | Result                                                 |
| ------------------------------------------------------- | ------------------------------------------------------ |
| `uv run ruff check .`                                   | Pass                                                   |
| `uv run mypy packages/core-py/src services/api/src ...` | Pass — 76 source files                                 |
| `uv run pytest -q`                                      | 199 passed, 100 skipped locally due sandboxed Postgres |
| `uv run python scripts/export_openapi.py --check`       | Pass                                                   |
| `pnpm run format:check`                                 | Pass                                                   |
| `pnpm run lint`                                         | Pass                                                   |
| `pnpm run typecheck`                                    | Pass                                                   |
| `pnpm run test`                                         | Pass — 34 JS tests                                     |
| `@agentrail/contracts check`                            | Pass                                                   |
| `@agentrail/contracts test`                             | Pass — 10 tests                                        |
| `pnpm build`                                            | Pass — web production build                            |
| `uv build --all-packages`                               | Pass                                                   |

Latest Phase 8 branch checks, run locally on 2026-07-27:

| Command                                                 | Result                                                 |
| ------------------------------------------------------- | ------------------------------------------------------ |
| `uv run ruff check .`                                   | Pass                                                   |
| `uv run mypy packages/core-py/src services/api/src ...` | Pass — 76 source files                                 |
| `uv run pytest -q`                                      | 199 passed, 103 skipped locally due sandboxed Postgres |
| `uv run python scripts/export_openapi.py --check`       | Pass                                                   |
| `pnpm run format:check`                                 | Pass                                                   |
| `pnpm run lint`                                         | Pass                                                   |
| `pnpm run typecheck`                                    | Pass                                                   |
| `pnpm run test`                                         | Pass — 34 JS tests                                     |
| `@agentrail/contracts check`                            | Pass                                                   |
| `@agentrail/contracts test`                             | Pass — 10 tests                                        |
| `pnpm build`                                            | Pass — web production build                            |
| `uv build --all-packages`                               | Pass                                                   |

Phase 8 review fixes, run locally on 2026-07-26 against Docker Compose PostgreSQL — the first
session in which the integration suite ran on this machine rather than skipping:

| Command                                             | Result                                             |
| --------------------------------------------------- | -------------------------------------------------- |
| `uv run pytest -q`                                  | **305 passed, 0 skipped** — full integration suite |
| `uv run ruff format --check .` / `ruff check .`     | Pass                                               |
| `uv run mypy packages/core-py/src services/api/src` | Pass — 63 source files                             |
| `uv run python scripts/export_openapi.py`           | Pass — snapshot unchanged                          |

The three new replay tests were confirmed to fail with the fixes reverted, so none of them is
vacuous.

---

## Known limitations

- **No PostgreSQL row-level security.** Tenant scoping is enforced in the application and tested
  there; RLS as defence in depth is Phase 14.
- **No durable per-organisation quota ledger.** Authenticated callers now have short-lived
  Redis-backed rate limits, but monthly/workload quotas are not implemented.
- **No invitations.** A user must have signed in once before they can be added to an organisation.
- **No API-key rotation or anomaly detection**, and no retention policy on the audit log (Phase 13).
- The execution runtime is deterministic/recorded and uses suite item counts; trajectory capture,
  replay records and the first programmatic evaluator are synthetic/deterministic. Live model
  evaluators and true live replay execution are not built yet.
- Canary deployment is simulated and metric-driven; there is no real deploy provider integration
  yet.
- Failed legacy jobs remain terminal; retry budgets, leases and outbox are implemented for
  evaluation run items.
- Correlation and trace identifiers propagate, but no spans are exported (Phase 13).
- Run-level metrics expose correlation/trace identifiers and SLO status, but external OpenTelemetry
  export and a hosted Collector are not wired yet.
- `dependency-review` warns and skips while the dependency graph is disabled, and is excluded from
  the required checks until that browser-only repository setting is enabled.

---

## Unresolved risks

- **Owner and admin currently share a permission set.** The "a key cannot out-rank its creator" guard
  is therefore untestable in its interesting direction. When the roles diverge — organisation
  deletion, billing — add the test that proves an admin cannot mint an owner key.
- **Audit immutability is by convention.** Nothing at the database level stops an operator with write
  access from editing a row. Consider an append-only trigger or a hash chain when the audit log
  becomes evidence rather than a convenience.
- **Sessions have no absolute lifetime cap.** A 14-day TTL is refreshed by nothing today, but if
  sliding expiry is ever added it needs an absolute ceiling too.
- **`packages/core-py` now owns identity as well as jobs.** Justified while both the API and worker
  need them, but this is the second table group to land there. A third should trigger extraction.
- **Local footgun:** `make integration` and `make compose-up-apps` share one database and the test
  fixtures truncate between tests. Documented in `docs/operations/LOCAL_DEVELOPMENT.md`; CI keeps
  them in separate jobs.

---

Phase 9 branch checks, run locally on 2026-07-26 against Docker Compose PostgreSQL 16.6:

| Command                                           | Result                                             |
| ------------------------------------------------- | -------------------------------------------------- |
| `uv run pytest -q`                                | **346 passed, 0 skipped** — full integration suite |
| `uv run ruff format --check .` / `ruff check .`   | Pass                                               |
| `uv run mypy` (4 source trees)                    | Pass — 79 source files                             |
| `uv run python scripts/export_openapi.py --check` | Pass                                               |
| `pnpm run test`                                   | Pass — 34 JS tests                                 |
| `pnpm run lint` / `typecheck` / `format:check`    | Pass                                               |
| `@agentrail/contracts check`                      | Pass                                               |
| `alembic upgrade head → downgrade 0008 → upgrade` | Clean                                              |

The four retry-sensitive tests were confirmed to fail with the ledger's deduplication defeated, so
none of them is vacuous.

Latest Phase 12 branch checks, run locally on 2026-07-27:

| Command                                                 | Result                                                 |
| ------------------------------------------------------- | ------------------------------------------------------ |
| `uv run ruff check .`                                   | Pass                                                   |
| `uv run mypy packages/core-py/src services/api/src ...` | Pass — 97 source files                                 |
| `uv run pytest -q`                                      | 289 passed, 170 skipped locally due sandboxed Postgres |
| `uv run python scripts/export_openapi.py --check`       | Pass                                                   |
| `pnpm run format:check`                                 | Pass                                                   |
| `pnpm run lint`                                         | Pass                                                   |
| `pnpm run typecheck`                                    | Pass                                                   |
| `pnpm run test`                                         | Pass — 46 JS tests                                     |
| `@agentrail/contracts check`                            | Pass                                                   |
| `@agentrail/contracts test`                             | Pass — 10 tests                                        |
| `pnpm build`                                            | Pass — web production build                            |
| `uv build --all-packages`                               | Pass after network approval for `hatchling` build deps |

Latest Phase 13 branch checks, run locally on 2026-07-27:

| Command                                                 | Result                                                 |
| ------------------------------------------------------- | ------------------------------------------------------ |
| `uv run ruff check .`                                   | Pass                                                   |
| `uv run mypy packages/core-py/src services/api/src ...` | Pass — 98 source files                                 |
| `uv run pytest -q`                                      | 291 passed, 172 skipped locally due sandboxed Postgres |
| `uv run python scripts/export_openapi.py --check`       | Pass                                                   |
| `pnpm run format:check`                                 | Pass                                                   |
| `pnpm run lint`                                         | Pass                                                   |
| `pnpm run typecheck`                                    | Pass                                                   |
| `pnpm run test`                                         | Pass — 46 JS tests                                     |
| `@agentrail/contracts check`                            | Pass                                                   |
| `@agentrail/contracts test`                             | Pass — 10 tests                                        |
| `pnpm build`                                            | Pass — web production build                            |
| `uv build --all-packages`                               | Pass after network approval for `hatchling` build deps |

Latest Phase 14 branch checks, run locally on 2026-07-27:

| Command                                                 | Result                                                 |
| ------------------------------------------------------- | ------------------------------------------------------ |
| `uv run ruff check .`                                   | Pass                                                   |
| `uv run mypy packages/core-py/src services/api/src ...` | Pass — 99 source files                                 |
| `uv run pytest -q`                                      | 291 passed, 175 skipped locally due sandboxed Postgres |
| `uv run python scripts/export_openapi.py --check`       | Pass                                                   |
| `pnpm run format:check`                                 | Pass                                                   |
| `pnpm run lint`                                         | Pass                                                   |
| `pnpm run typecheck`                                    | Pass                                                   |
| `pnpm run test`                                         | Pass — 46 JS tests                                     |
| `@agentrail/contracts check`                            | Pass                                                   |
| `pnpm build`                                            | Pass — web production build                            |
| `uv build --all-packages`                               | Pass after network approval for `hatchling` build deps |

---

## Next tasks (Phase 14 — Security and supply chain)

1. Open the Phase 14 pull request and let CI run the PostgreSQL-backed security tests plus
   `containers / scan`.
2. If the new container check is stable, add it to the live required status checks.
3. Continue Phase 14 with PostgreSQL RLS and durable quota/retention work.

**Exit criteria:** cross-tenant tests pass across all surfaces; security workflows green; green PR.

---

## Owner actions required

1. **Enable the dependency graph** at
   [`Settings → Code security and analysis`](https://github.com/JCHETAN26/AgentRail/settings/security_analysis).
   Still the only outstanding item, and still browser-only — there is no REST field for it. Enabling
   it turns `dependency-review` from a warning-only skip into a real advisory/licence gate, unblocks
   Dependabot security alerts, and allows adding that check to the required list in
   `docs/BRANCH_PROTECTION.md`.
