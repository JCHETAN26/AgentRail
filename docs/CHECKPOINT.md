# Checkpoint

Operational handoff between sessions. This file is the first thing to read when resuming work.

---

## Current state

|                |                                                                                     |
| -------------- | ----------------------------------------------------------------------------------- |
| **Phase**      | 2 — CloudOps sandbox and contracts                                                  |
| **Status**     | In progress on branch `feat/p02-cloudops-sandbox`                                   |
| **Base**       | `main` @ `a37e1f0` (Phase 1 merged)                                                 |
| **Next phase** | 3 — Agent registry, after Phase 2 exits                                             |
| **Guardrails** | Branch protection live on `main`; direct pushes rejected; 10 required status checks |

Phase 0 shipped in PR [#1](https://github.com/JCHETAN26/AgentRail/pull/1); housekeeping (MIT licence,
applied branch protection, Dependabot triage) in [#19](https://github.com/JCHETAN26/AgentRail/pull/19).
Phase 1 shipped in PR [#20](https://github.com/JCHETAN26/AgentRail/pull/20).

---

## Read these first

1. `BUILDPLAN.md` §4 — CloudOps tool list, incident families and ground-truth requirements
2. `services/cloudops-sandbox/src/agentrail_cloudops_sandbox/cloudops.py` — Phase 2 synthetic data,
   tool contracts, scenarios and idempotency behavior
3. `services/cloudops-sandbox/src/agentrail_cloudops_sandbox/app.py` — HTTP surface for the sandbox
4. `services/cloudops-sandbox/tests/test_app.py` — contract tests for tools, scenarios, faults and
   idempotency
5. `docs/adr/0006-delegated-authentication-and-tenant-scoping.md` — still the auth/tenant boundary
6. `docs/security/THREAT_MODEL.md` — 27 threats, with what is _not_ mitigated stated plainly

---

## Completed capabilities (through Phase 1)

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

| Revision           | Description                                                                                                                                                                      |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0001_create_jobs` | Creates `jobs` with check constraints and `ix_jobs_state_created_at`                                                                                                             |
| `0002_identity`    | Adds users, organisations, memberships, projects, sessions, api_keys, audit_events; retrofits `jobs.project_id`; moves idempotency uniqueness to `(project_id, idempotency_key)` |

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

---

## Known limitations

- **No rate limiting or quotas.** An authenticated caller can create unbounded work (Phase 14).
- **No PostgreSQL row-level security.** Tenant scoping is enforced in the application and tested
  there; RLS as defence in depth is Phase 14.
- **No invitations.** A user must have signed in once before they can be added to an organisation.
- **No API-key rotation or anomaly detection**, and no retention policy on the audit log (Phase 13).
- The sandbox now exposes Phase 2's synthetic tool surface, but no agent runtime consumes it yet.
- Failed jobs remain terminal — no retry budget, leases or outbox (Phase 5).
- Correlation and trace identifiers propagate, but no spans are exported (Phase 13).
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

## Next tasks (Phase 2 — CloudOps sandbox and contracts)

1. Decide whether the sandbox OpenAPI should become a committed generated contract beside the
   platform API snapshot, or whether the current tool-contract endpoint is the only Phase 2 contract
   artifact.
2. Run full-repo verification after the Phase 2 docs and sandbox changes.
3. Open the Phase 2 pull request once verification is green.

**Exit criteria:** 25 deterministic scenarios; a duplicate side-effect key returns the original
result; green PR.

---

## Owner actions required

1. **Enable the dependency graph** at
   [`Settings → Code security and analysis`](https://github.com/JCHETAN26/AgentRail/settings/security_analysis).
   Still the only outstanding item, and still browser-only — there is no REST field for it. Enabling
   it turns `dependency-review` from a warning-only skip into a real advisory/licence gate, unblocks
   Dependabot security alerts, and allows adding that check to the required list in
   `docs/BRANCH_PROTECTION.md`.
