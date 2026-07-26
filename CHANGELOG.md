# Changelog

All notable changes to AgentRail are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project will adopt
[Semantic Versioning](https://semver.org/) at its first release.

## [Unreleased]

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
terminal with no retry budget; no published benchmark numbers; no licence chosen.
