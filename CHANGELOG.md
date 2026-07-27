# Changelog

All notable changes to AgentRail are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project will adopt
[Semantic Versioning](https://semver.org/) at its first release.

## [Unreleased]

### Added — Phase 9: failure injection and reliability

- Deterministic fault profiles covering all 23 model, tool and platform fault families from the
  build plan, selected by item index and attempt number rather than by RNG, so a faulted run
  reproduces exactly like a clean one.
- Transient faults retry; reasoning failures (refusal, wrong tool, invalid arguments, malformed
  output) go terminal on first occurrence, because they reproduce identically on a second attempt.
- Suite `fault_profiles`, carried untyped since Phase 4, are now validated and executed. An
  unexecutable profile is rejected with its index rather than silently never firing.
- Tool-call, token, loop, latency and cost budgets, with a `budget_exhausted` terminal reason.
- Circuit breaker per dependency: opens on consecutive failures, half-opens after a cooldown with a
  reset streak, and reopens on a single failed probe. Implemented and unit-tested, but not yet wired
  to a caller — the recorded executor has no live dependency to trip it against.
- **Side-effect ledger** with a `UNIQUE` constraint on an attempt-independent idempotency key, so a
  duplicate side effect is impossible at the database level rather than merely untested. Plus
  Alembic revision `0009_failure_injection`.
- Recovery API at `/api/v1/evaluation-runs/{run_id}/recovery`: per-item attempts, lease expiry,
  injected fault, budget spend and side-effect count.
- Chaos commands — `make chaos-duplicate`, `chaos-strand`, `chaos-report` — to force duplicate
  delivery and lease expiry against a running stack and report duplicates.
- Zero-duplicate-side-effect coverage under retry, duplicate delivery, two racing workers, lease
  expiry after a partial attempt, and direct constraint violation.

### Fixed — Phase 9 review

- Budgets are per item, not per attempt. A retry now resumes the previous attempt's spend instead of
  restarting at zero, which had let an item with two attempts spend twice its limit and still report
  the smaller number.
- Unexecutable fault profiles are rejected when the suite is created, with the offending index.
  Previously the failure surfaced only when the worker parsed the profile, after the item had been
  leased and with nothing catching it — which stranded the item and stopped the consumer.
- A profile written before that validation existed now fails its item with `fault_profile_invalid`
  rather than propagating out of the worker's consume loop.

### Added — Phase 8: replay and time travel

- PostgreSQL-backed trajectory replay records, plus Alembic revision `0008_trajectory_replays`.
- Recorded, live-labelled and forked replay creation from persisted trajectories and checkpoints.
- Deterministic source/replay digests for replay reproduction and fork divergence.
- Replay safety summaries that explicitly prevent original side-effect repetition.
- Replay APIs:
  `/api/v1/trajectories/{trajectory_id}/replays` for create and list.
- Audit events and tenant-isolation coverage for replay creation.

### Fixed — Phase 8 review

- Replay creation now requires `run:create`. It persists a row and an audit event, so authorising it
  as a read let a viewer or a read-scoped API key write.
- Fork digests are taken over the raw overrides rather than the redacted copy, so two forks differing
  only in a sensitive-keyed value no longer collapse onto one digest and report no divergence. Only
  the redacted copy is persisted or returned.
- A recorded replay sent with `fork_overrides` is now rejected with `validation_failed` instead of
  silently ignoring them and recording `reproduced: true`.
- `safety_summary` reports the replay that actually ran, not the mode requested: no mode invokes a
  model today, so `live_model_calls` is `0` and `executed_live` is `false` for live-labelled records.

### Added — Phase 7: evaluators and comparison

- PostgreSQL-backed evaluator versions, per-item evaluator results and comparison reports, plus
  Alembic revision `0007_evaluators_comparison`.
- Deterministic programmatic scoring during run aggregation, with execution errors kept in
  denominators.
- Comparison APIs:
  `/api/v1/evaluation-runs/{run_id}/comparison` and
  `/api/v1/evaluation-runs/{run_id}/evaluator-results`.
- Aggregate evaluator, category and regression summaries with reproducible suite/evaluator digests.
- Contract, aggregation and tenant-isolation coverage for comparison reads.

### Added — Phase 6: trajectory capture and trace explorer

- PostgreSQL-backed trajectories, ordered trajectory steps and named checkpoints, plus Alembic
  revision `0006_trajectories`.
- Deterministic worker capture for each evaluation run item, including input, graph-state, tool-call,
  evidence, checkpoint and final-result steps.
- Recursive redaction for sensitive keys and email addresses before trajectory payloads are stored.
- Trace explorer APIs:
  `/api/v1/evaluation-runs/{run_id}/items`, `/api/v1/trajectories/{trajectory_id}`,
  `/api/v1/trajectories/{trajectory_id}/steps` and
  `/api/v1/trajectories/{trajectory_id}/checkpoints`.
- Tenant-scoped trajectory reads, step-type filters and failed-item links to the exact failing step.
- Contract, redaction and integration coverage for trajectory visibility and cross-tenant denial.

### Added — Phase 5: durable distributed execution

- Evaluation run and run-item state machines with terminal-state guards, retryable item failures and
  cancellation semantics.
- PostgreSQL-backed `evaluation_runs`, `run_items` and `outbox_events` tables, plus Alembic revision
  `0005_durable_execution`.
- Idempotent run creation from frozen evaluation suites, with transactional run-item expansion and a
  durable outbox event committed before Redis delivery.
- Evaluation-run APIs for create, fetch, cancellation and server-sent progress snapshots.
- Worker support for the run queue, duplicate-safe run claims, item leases, retry-budget recovery,
  PostgreSQL checkpoints and aggregation into passed/failed/cancelled outcomes.
- Contract and integration coverage for idempotency replay, frozen-suite enforcement, tenant denial,
  100-item completion, duplicate delivery and expired-lease recovery.

### Added — Phase 4: dataset ingestion and suite builder

- Project-scoped datasets with stable slugs and tenant-aware create/list APIs.
- Immutable dataset versions for JSONL and CSV uploads, with content digests, storage URIs,
  validation reports, record schema metadata, item counts and partition counts.
- Actionable rejection reports for malformed dataset input, including line/record locations and
  missing-field details.
- Evaluation suites that bind a dataset version to evaluator configuration, thresholds, fault
  profiles and a preview summary.
- Freeze endpoint for suites; repeated freezes are idempotent and preserve the original timestamp.
- Contract, parser and integration tests for validation, duplicate-content rejection, cross-tenant
  denial and suite freezing.

### Added — Phase 3: agent registry and immutable versions

- Project-scoped agent definitions with stable slugs and tenant-aware create/list APIs.
- Immutable agent versions with graph spec, prompt bundle, model configuration, tool contracts,
  policy bundle, optional source commit and a canonical SHA-256 content digest.
- Agent registry APIs:
  `/api/v1/projects/{project_id}/agents`, `/api/v1/agents/{agent_id}/versions` and
  `/api/v1/agent-versions/{version_id}`.
- Registry audit events for created agents and versions.
- Contract and integration tests for version numbering, duplicate-content rejection and
  cross-tenant denial.

### Added — Phase 2: CloudOps sandbox and contracts

- CloudOps sandbox tool contracts for all ten build-plan tools, including risk, side-effect class,
  approval requirement and idempotency-key requirement metadata.
- Synthetic service health, metrics, logs, dependency graphs and runbooks for the deterministic
  reference workload.
- Idempotent side-effecting sandbox tools for restart, scale, incident creation and on-call
  notification. Reusing an idempotency key returns the original result and marks the call as a
  replay.
- Reset and seed endpoints for deterministic scenario setup.
- Fault-injection hooks for latency, timeout, HTTP 500, malformed, stale, rate limit and unavailable
  responses.
- Twenty-five scenario manifests covering the 16 incident families from the build plan, each with
  expected diagnosis, allowed and forbidden tools, expected arguments, remediation and approval
  flags, evidence, budgets and final disposition.

### Added — Phase 1: authentication, organisations and tenancy

**Identity**

- Delegated sign-in behind one provider protocol: a deterministic development provider that needs no
  credentials and no network (used by local development, CI and the demo) and GitHub OAuth with
  `state` verification for deployed environments. The development provider is structurally
  unavailable once deployed, and a deployed environment without OAuth credentials refuses to start.
- Opaque server-side sessions: 256-bit tokens persisted only as SHA-256 digests, delivered in an
  `HttpOnly`, `SameSite=Lax` cookie that is `Secure` when deployed, and revoked on sign-out.
- Scoped API keys of the form `ar_<key_id>_<secret>`, stored only as digests and compared in constant
  time. A key is bounded by both a role and optional scopes, is revocable, may expire, and its token
  is returned exactly once.

**Tenancy and authorisation**

- Organisations, memberships and projects, with five roles forming a strict capability ladder.
- One central `authorize()` function, pure and exhaustively unit-tested, used by every route. Tenancy
  is checked before permission and both failures are indistinguishable, so another tenant's resource
  returns `403` and never `404`.
- Append-only audit events with redacted context and the originating correlation id.

**API**

- `/api/v1/auth/*` for sign-in, sign-out and the current caller; `/api/v1/organisations/*` for
  organisations, members, projects, API keys and audit events.

**Console**

- Sign-in, tenant context with an organisation picker, first-organisation onboarding, sign-out, and
  complete signed-out, loading, empty and permission-denied states.

### Changed

- **Breaking:** jobs moved from `/api/v1/jobs` to `/api/v1/projects/{project_id}/jobs`. There is no
  unscoped job listing, and `GET /api/v1/jobs/{job_id}` now authorises against the job's project.
- **Breaking:** idempotency keys are unique per project rather than globally, so two tenants may use
  the same key without colliding — and without one being able to detect the other's use.
- Every `/api/v1` endpoint except sign-in now requires a credential.
- CORS sends credentials, which makes the explicit origin allowlist load-bearing.
- The console moved to port **3100**. Next.js's default 3000 collided with unrelated local projects,
  and Playwright's `reuseExistingServer` silently ran the entire end-to-end suite against one.

### Security

- Threat model expanded to 27 entries. T13 (unauthenticated access), T14 (cross-tenant access) and
  T10 (cross-origin access) close; T21–T27 are added for session theft, sign-out survival, key
  leakage, privilege escalation, development sign-in reaching production, OAuth callback forgery and
  audit-log integrity. T15 (denial of service) remains **not mitigated** — there is still no rate
  limiting.

### Added — post-Phase-0 housekeeping

- MIT licence.
- Branch protection applied to `main` and verified: pull requests required, ten required status
  checks, linear history, force-pushes and deletions blocked, enforced for administrators.
  `docs/BRANCH_PROTECTION.md` records the configuration and three documented deviations from the
  build plan.

### Changed

- Dependency updates from Dependabot's opening wave: `psycopg` 3.2.3 → 3.3.4, `uvicorn` 0.34.0 →
  0.51.0, `pydantic-settings` 2.7.1 → 2.14.2, the Python tooling group (Ruff, mypy, pytest),
  `@tanstack/react-query` 5.62.11 → 5.101.4, `@playwright/test` 1.49.1 → 1.62.0, `@types/node`
  22.10.5 → 26.1.1, and the GitHub Actions and `uv` container images.
- Four updates were rejected rather than taken: a Python 3.14 base image (violates the workspace's
  `requires-python <3.13` pin), `redis` 5 → 8, and the grouped React and tooling updates. All four
  failed CI; see the closing comments on pull requests #8, #13, #14 and #15.

### Added — Phase 0: repository, product contract and guardrails

**Foundation**

- pnpm workspace (`apps/*`, `packages/*`) and uv workspace (`packages/core-py`, `services/*`) with
  both lockfiles committed and pinned toolchains.
- Strict TypeScript and strict mypy across every package; Ruff for Python formatting and linting.
- `Makefile` with `bootstrap`, `dev`, `verify`, `format`, `lint`, `typecheck`, `test`, `integration`,
  `e2e`, `build`, `contracts`, `migrate`, `compose-up`, `compose-down` and `clean`.

**Services**

- `services/api` — FastAPI platform API with `/healthz`, `/readyz` and the `/api/v1/jobs` resource
  (create, fetch, cursor-paginated list).
- `services/worker` — Redis consumer with conditional-update job claiming, a recovery sweep for jobs
  stranded in `PENDING`, an HTTP health surface and graceful SIGTERM shutdown.
- `services/cloudops-sandbox` — deterministic, synthetic tool surface exposing one no-op task.
- `packages/core-py` — shared settings, JSON logging with redaction, correlation and W3C trace
  propagation, database and Redis clients, and the job table and state machine.
- `packages/contracts` — committed OpenAPI snapshot and the TypeScript types generated from it.
- `apps/web` — Next.js 15 console that submits a job, polls it, and displays the result with loading,
  empty, error and failure states.

**Data**

- Alembic migration `0001_create_jobs` creating the `jobs` table with a unique idempotency key, state
  and completion-time check constraints, and an index supporting the recovery sweep.

**Infrastructure and CI**

- Docker Compose stack: PostgreSQL, Redis, MinIO, a one-shot migration job, the three services, and an
  optional observability profile with an OpenTelemetry Collector.
- Single parameterised Dockerfile for all Python services; images run as an unprivileged user.
- `ci` workflow with `frontend`, `python`, `contracts`, `integration`, `e2e` and `build` jobs, plus
  `codeql` and `dependency-review`. No job requires a model-provider credential.
- Pull-request and issue templates, `CODEOWNERS` and Dependabot configuration.

**Documentation**

- Architecture overview, threat model, local development guide, branch-protection guide, contributor
  guide, security policy, checkpoint, and ADRs 0001–0005.

### Known limitations

No authentication, organisations or tenancy; one deterministic sandbox task rather than the full
CloudOps environment; no agent registry, evaluation, replay, policy engine, release gates or canary
deployment; correlation identifiers are propagated but no spans are exported; failed jobs are
terminal with no retry budget; no published benchmark numbers.
