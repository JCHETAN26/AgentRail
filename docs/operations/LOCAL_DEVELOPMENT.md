# Local development

## Prerequisites

| Tool     | Version | Notes                                               |
| -------- | ------- | --------------------------------------------------- |
| Docker   | 27+     | With Compose v2                                     |
| Node     | 22.12+  | `.nvmrc` pins the version                           |
| pnpm     | 9.15.9  | `corepack enable` or install globally               |
| uv       | 0.11+   | Manages Python 3.12 itself; no system Python needed |
| GNU Make | any     | Task runner                                         |

```bash
make bootstrap    # installs both toolchains and creates .env
```

## Two ways to run

### Everything in containers

```bash
make compose-up-apps          # sandbox, one-shot migration job, API, worker
pnpm --filter @agentrail/web dev
```

The web app runs on the host because a hot-reloading Next.js dev server in a container adds friction
for no benefit.

### Services from source

```bash
make compose-up               # PostgreSQL, Redis, MinIO only
make migrate
make dev                      # sandbox + API + worker + web, all in the foreground
```

`make dev` runs the four processes under one shell and stops them together on Ctrl-C.

## Ports

Deliberately non-default so the stack coexists with other local projects. Override any of them in
`.env`.

| Service             | Port        | Override                                               |
| ------------------- | ----------- | ------------------------------------------------------ |
| Web console         | 3000        | —                                                      |
| Platform API        | 8000        | `AGENTRAIL_API_PORT`                                   |
| CloudOps sandbox    | 8100        | `AGENTRAIL_SANDBOX_PORT`                               |
| Worker health       | 8200        | `AGENTRAIL_WORKER_HEALTH_PORT`                         |
| PostgreSQL          | 5433        | `AGENTRAIL_POSTGRES_PORT`                              |
| Redis               | 6381        | `AGENTRAIL_REDIS_PORT`                                 |
| MinIO API / console | 9002 / 9003 | `AGENTRAIL_MINIO_PORT`, `AGENTRAIL_MINIO_CONSOLE_PORT` |

## Configuration

All configuration is read once at start-up through pydantic-settings, from `AGENTRAIL_`-prefixed
environment variables and `.env`. Reading `os.environ` directly anywhere in a service is a bug: it
bypasses validation.

`.env` is created from `.env.example` by `make bootstrap` and is git-ignored. Every value in
`.env.example` is a local-only default; none of them is valid anywhere else.

> **Note:** settings resolve `.env` relative to the working directory, so run `make` targets from the
> repository root. `make migrate` passes an absolute Alembic config path for this reason.

## Tests

```bash
make test          # unit tests; integration tests skip if PostgreSQL/Redis are absent
make integration   # requires make compose-up
make e2e           # requires make compose-up-apps; installs Chromium on first run
make verify        # exactly what CI runs that needs no services
```

Integration tests apply the real Alembic migrations and truncate `jobs` between tests. They use a
queue key unique to the test session, so they never consume messages from a stack you have running.

Setting `AGENTRAIL_REQUIRE_INTEGRATION=1` turns a missing dependency from a skip into a failure. CI
sets it; do the same locally when you want to be certain the integration tests actually ran.

## Debugging

Every response carries `x-correlation-id` and `traceparent`. The id is stored on the job row, so:

```bash
docker compose -f infra/compose/docker-compose.yml logs api worker \
  | grep cid_0f3c...
```

returns every line from every service for that request. Logs are single-line JSON — pipe them through
`jq` for readability.

Health endpoints:

```bash
curl -s localhost:8000/healthz    # liveness: touches nothing
curl -s localhost:8000/readyz     # readiness: reports each dependency separately
curl -s localhost:8200/readyz     # worker, including its view of the sandbox
```

`/healthz` deliberately does not check dependencies — otherwise a Redis blip would restart healthy
containers instead of removing them from rotation.

## Common problems

**`make` fails with an SDK error on macOS.** A stale `SDKROOT` exported by your shell profile breaks
the `make` shim. Check with `echo $SDKROOT`; if it names a directory that no longer exists, remove
the export from `~/.zshrc` or run `unset SDKROOT`.

**Migrations connect to the wrong database.** You ran Alembic from `services/api`, where `.env` is not
found and the default `localhost:5432` applies. Use `make migrate` from the repository root.

**Integration tests skip silently.** PostgreSQL or Redis is not reachable. Run `make compose-up`, then
re-run with `AGENTRAIL_REQUIRE_INTEGRATION=1` to see the reason instead of a skip.

**Port already in use.** Another project holds the port. Override it in `.env`.

**End-to-end tests pass but assert on the wrong application.** Playwright reuses an already-running
server when one is listening. The console therefore runs on **3737**, not Next.js's default 3000 —
that default once caused the whole suite to run against an unrelated project's dev server and fail
with a confusing "cannot find the Email field".

**Integration tests fail in strange ways while the app stack is running.** `make integration` and
`make compose-up-apps` share one PostgreSQL database, and the test fixtures truncate every table
between tests. Run one or the other, not both. CI keeps them in separate jobs, so this only bites
locally.

## Resetting

```bash
make compose-down    # stop, keep data
make clean           # stop, delete volumes, caches and build outputs
```
