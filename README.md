# AgentRail

An internal developer platform for treating an AI agent as a versioned, testable, observable and
deployable software artefact.

The question AgentRail exists to answer:

> How can a team prove that an agent change is safer, more reliable and more useful **before**
> exposing it to users?

> [!NOTE] > **Status: Phase 0 of 18.** The repository currently contains the monorepo foundation and one
> complete deterministic request path. Agent registry, evaluation, replay, policy, release gates
> and canary deployment are not built yet. Nothing in this README describes a capability that
> does not exist — see [Known limitations](#known-limitations).

---

## What works today

A single deterministic vertical slice, end to end:

```text
Web console  ──POST /api/v1/jobs──▶  Platform API  ──row──▶  PostgreSQL   (authoritative)
                                          │
                                          └──job id──▶  Redis  ──▶  Worker
                                                                      │
                                                        CloudOps sandbox (deterministic)
                                                                      │
      Web console  ◀──poll GET /api/v1/jobs/{id}──  PostgreSQL  ◀──result
```

Every hop carries a correlation id and a W3C `traceparent`. The result is a SHA-256 digest of the
submitted message, so a reviewer can verify the payload survived all five hops unmodified. No model
provider is involved, so the path is byte-for-byte reproducible and costs nothing to run.

| Component         | Path                        | What it does                                                 |
| ----------------- | --------------------------- | ------------------------------------------------------------ |
| Web console       | `apps/web`                  | Next.js 15 + React 19, strict TypeScript, TanStack Query     |
| Platform API      | `services/api`              | FastAPI, SQLAlchemy 2, Alembic, idempotent job creation      |
| Worker            | `services/worker`           | Redis consumer, conditional-update claims, graceful shutdown |
| CloudOps sandbox  | `services/cloudops-sandbox` | Synthetic, deterministic tool surface                        |
| Shared primitives | `packages/core-py`          | Settings, JSON logging, correlation, job state machine       |
| Contracts         | `packages/contracts`        | OpenAPI snapshot and generated TypeScript types              |

---

## Quick start

Requirements: Docker, Node 22.12+, [pnpm](https://pnpm.io) 9, [uv](https://docs.astral.sh/uv/), and
GNU Make.

```bash
make bootstrap        # install toolchains, create .env from .env.example
make compose-up       # PostgreSQL, Redis, MinIO
make migrate          # apply database migrations
make verify           # formatting, lint, strict types, unit tests, contract drift
```

Run the whole stack in containers and open the console at <http://localhost:3000>:

```bash
make compose-up-apps  # sandbox, migrations, API and worker
pnpm --filter @agentrail/web dev
```

Or run the services from source on the host:

```bash
make dev              # sandbox + API + worker + web, all in the foreground
```

### Verifying the path by hand

```bash
curl -s -X POST localhost:8000/api/v1/jobs \
  -H 'content-type: application/json' \
  -H 'Idempotency-Key: demo-1' \
  -d '{"message":"phase zero"}'

curl -s localhost:8000/api/v1/jobs/<returned id>
```

Replaying the same idempotency key returns `200` with the original job rather than creating a
second one. Replaying it with a _different_ body returns `409 idempotency_key_reused`.

---

## Commands

| Command                                                | Purpose                                                                                    |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| `make bootstrap`                                       | Install every toolchain dependency and create `.env`                                       |
| `make verify`                                          | Everything CI runs that needs no services: format, lint, types, unit tests, contract drift |
| `make test`                                            | Unit tests only (integration tests skip when dependencies are absent)                      |
| `make integration`                                     | Tests against real PostgreSQL and Redis                                                    |
| `make e2e`                                             | Playwright tests against a running stack                                                   |
| `make contracts`                                       | Regenerate the OpenAPI snapshot and the TypeScript client                                  |
| `make migrate`                                         | Apply database migrations                                                                  |
| `make compose-up` / `compose-up-apps` / `compose-down` | Manage the local stack                                                                     |
| `make build`                                           | Build the web app and Python distributions                                                 |
| `make clean`                                           | Remove build outputs, caches and local volumes                                             |

`make help` lists them all.

---

## Design commitments

These hold from Phase 0 onward and are enforced by tests, not by convention:

- **PostgreSQL is authoritative.** Redis carries delivery only. A job row is committed _before_ its
  identifier is published, and losing Redis delays work rather than losing it.
- **Retries are safe.** Delivery is at-least-once. Every state change is a conditional
  `UPDATE ... WHERE state = <expected>`, so duplicate delivery, racing workers and late events
  cannot cause a second execution. Terminal states have no outgoing transitions at all.
- **Errors are a contract.** Every non-2xx response is a `ProblemDetail` with a stable machine-readable
  `code` and a `correlation_id`. Stack traces never reach a client.
- **Secrets never reach logs.** The JSON log formatter redacts any field whose key looks sensitive
  before serialisation.
- **The demo needs no paid key.** The deterministic path is the default, not a fallback.

---

## Documentation

| Document                                                                       | Contents                                      |
| ------------------------------------------------------------------------------ | --------------------------------------------- |
| [`docs/architecture/OVERVIEW.md`](docs/architecture/OVERVIEW.md)               | Services, data flow, boundaries               |
| [`docs/adr/`](docs/adr/)                                                       | Architecture decision records                 |
| [`docs/security/THREAT_MODEL.md`](docs/security/THREAT_MODEL.md)               | Trust boundaries and mitigations              |
| [`docs/operations/LOCAL_DEVELOPMENT.md`](docs/operations/LOCAL_DEVELOPMENT.md) | Running and debugging locally                 |
| [`docs/BRANCH_PROTECTION.md`](docs/BRANCH_PROTECTION.md)                       | Required `main` branch settings               |
| [`docs/CHECKPOINT.md`](docs/CHECKPOINT.md)                                     | Current phase state and next tasks            |
| [`CONTRIBUTING.md`](CONTRIBUTING.md)                                           | Workflow, conventions and review expectations |

---

## Known limitations

Deliberate, and scheduled:

- No authentication, organisations or tenancy yet — everything is single-tenant and unauthenticated
  (Phase 1).
- The CloudOps sandbox executes one deterministic no-op task. The synthetic services, metrics, logs,
  runbooks and 16 incident families are Phase 2.
- No agent registry, evaluation suites, trajectories, replay, policy engine, release gates or canary
  deployment yet (Phases 3–12).
- Correlation and trace identifiers are propagated, but no spans are exported. The OpenTelemetry SDK
  and Collector pipeline are Phase 13.
- Failed jobs are terminal: there is no retry budget or transactional outbox yet (Phase 5). A
  periodic sweep re-publishes jobs stranded in `PENDING`.
- No published benchmark numbers. Benchmarks are Phase 17, and no metric will be quoted before it is
  generated from a frozen test set.
- No `LICENSE` file yet — the licence has not been chosen.

The CloudOps sandbox is **synthetic**. It models no real infrastructure and its output must never be
described as production telemetry.
