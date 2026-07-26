# Checkpoint

Operational handoff between sessions. This file is the first thing to read when resuming work.

---

## Current state

|                |                                                                                     |
| -------------- | ----------------------------------------------------------------------------------- |
| **Phase**      | 1 — Authentication, organisations and tenancy                                       |
| **Status**     | Complete, in review on branch `feat/p01-auth-and-tenancy`                           |
| **Base**       | `main` @ `77e20f6` (Phase 0 merged, plus licence and guardrails)                    |
| **Next phase** | 2 — CloudOps sandbox and contracts. **Do not start until this PR is merged.**       |
| **Guardrails** | Branch protection live on `main`; direct pushes rejected; 10 required status checks |

Phase 0 shipped in PR [#1](https://github.com/JCHETAN26/AgentRail/pull/1); housekeeping (MIT licence,
applied branch protection, Dependabot triage) in [#19](https://github.com/JCHETAN26/AgentRail/pull/19).

---

## Read these first

1. `docs/adr/0006-delegated-authentication-and-tenant-scoping.md` — the whole Phase 1 design
2. `packages/core-py/src/agentrail_core/identity/roles.py` — the one function that decides access
3. `services/api/src/agentrail_api/auth/service.py` — credential → actor → principal
4. `services/api/tests/test_tenancy.py` — the isolation guarantee, asserted
5. `docs/security/THREAT_MODEL.md` — 27 threats, with what is _not_ mitigated stated plainly
6. `docs/adr/0002-postgresql-authoritative-redis-delivery.md` — still the core reliability decision

---

## Completed capabilities (Phase 1)

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

---

## Known limitations

- **No rate limiting or quotas.** An authenticated caller can create unbounded work (Phase 14).
- **No PostgreSQL row-level security.** Tenant scoping is enforced in the application and tested
  there; RLS as defence in depth is Phase 14.
- **No invitations.** A user must have signed in once before they can be added to an organisation.
- **No API-key rotation or anomaly detection**, and no retention policy on the audit log (Phase 13).
- The sandbox still runs one deterministic no-op task (Phase 2).
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

Only after this pull request is merged:

1. Branch `feat/p02-cloudops-sandbox` from the merged `main`.
2. Synthetic services, metrics, logs, runbooks and incidents in the sandbox.
3. The ten tool schemas from the build plan, with risk and side-effect classifications.
4. Idempotent writes with an idempotency key on every side-effecting tool.
5. Fault-injection hooks (latency, timeout, 500, malformed, stale, rate limit, unavailable).
6. Scenario manifests and ground truth: expected diagnosis, allowed and forbidden tools, expected
   arguments, whether remediation is permitted, approval requirements, evidence, budgets.
7. Reset and seed commands.
8. At least 25 deterministic scenarios.

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
2. Review and merge the Phase 1 pull request.
