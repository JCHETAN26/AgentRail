# Checkpoint

Operational handoff between sessions. This file is the first thing to read when resuming work.

---

## Current state

|                  |                                                                                          |
| ---------------- | ---------------------------------------------------------------------------------------- |
| **Phase**        | 0 — Repository, product contract and guardrails                                          |
| **Status**       | Complete, awaiting review                                                                |
| **Branch**       | `feat/p00-foundation`                                                                    |
| **Pull request** | See the PR opened from this branch (draft until CI is green)                             |
| **Base**         | `main` @ `a07219b` (`chore: initialize repository`)                                      |
| **Next phase**   | 1 — Authentication, organisations and tenancy. **Do not start until this PR is merged.** |

`main` was bootstrapped with a `.gitignore` and `README.md` only, because the GitHub repository was
empty and a pull request needs a base branch to exist. Per the repository owner's instruction, the
planning documents (`BUILDPLAN.md`, `SYSTEM_PROMPT.md`, `CLAUDE_CODE_MASTER_PROMPT.md`) are
git-ignored and are **not** published to the remote.

---

## Read these first

1. `docs/architecture/OVERVIEW.md` — services, request path, layer boundaries
2. `docs/adr/0002-postgresql-authoritative-redis-delivery.md` — why the commit precedes the publish
3. `docs/adr/0003-idempotency-and-conditional-updates.md` — how duplicate work is prevented
4. `packages/core-py/src/agentrail_core/jobs/state.py` — the state machine every later phase extends
5. `services/worker/src/agentrail_worker/runner.py` — the claim/execute/complete pattern
6. `docs/security/THREAT_MODEL.md` — what is mitigated and what is deferred

---

## Completed capabilities

- pnpm and uv workspaces with pinned toolchains and committed lockfiles.
- Strict TypeScript and strict mypy; Ruff formatting and linting; Prettier for the JS/TS tree.
- Four services: web console, platform API, worker, CloudOps sandbox — each with tested liveness and
  readiness behaviour and clean shutdown.
- A complete deterministic vertical slice: web → API → PostgreSQL → Redis → worker → sandbox →
  PostgreSQL → web, with no model provider involved.
- Idempotent job creation with request fingerprinting; conditional-update job claiming.
- Correlation id and W3C `traceparent` propagation across every hop, persisted on the job row.
- Structured JSON logging with automatic redaction of sensitive keys.
- Committed OpenAPI snapshot and generated TypeScript types, with drift enforced in CI.
- Docker Compose stack with a separate one-shot migration job; images run as an unprivileged user.
- CI covering frontend, python, contracts, integration, e2e and container builds; plus CodeQL and
  dependency review. No job needs a paid credential.

---

## Architecture decisions taken

| ADR  | Decision                                                                             |
| ---- | ------------------------------------------------------------------------------------ |
| 0001 | Single monorepo; pnpm and uv workspaces; one parameterised Dockerfile                |
| 0002 | PostgreSQL authoritative, Redis delivery-only, commit-before-publish, recovery sweep |
| 0003 | Idempotency keys at the edge, conditional `UPDATE ... WHERE state` in the core       |
| 0004 | Propagate correlation and trace identifiers now; export spans in Phase 13            |
| 0005 | API generates the contract; committed snapshot makes drift a CI failure              |

---

## Migrations

| Revision           | Description                                                                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `0001_create_jobs` | Creates `jobs` with a unique idempotency key, state and completion-time check constraints, and `ix_jobs_state_created_at` for the recovery sweep |

`downgrade` is implemented and exercised in CI (`ci / integration` runs `downgrade base` then
`upgrade head`).

---

## Verification evidence

Run on 2026-07-26 against Docker Compose (PostgreSQL 16.6, Redis 7.4) on macOS 15 / arm64.

| Command                                      | Result                                                                      |
| -------------------------------------------- | --------------------------------------------------------------------------- |
| `make verify`                                | Pass — formatting, lint, strict types, 188 unit tests, contract drift check |
| `uv run pytest`                              | 165 passed                                                                  |
| `pnpm run test`                              | 23 passed (14 web, 9 contracts)                                             |
| `make integration`                           | 32 passed, 133 deselected                                                   |
| `make e2e`                                   | 3 passed (Chromium, against the Compose stack)                              |
| `docker compose --profile apps up -d --wait` | All six services healthy                                                    |
| `pnpm --filter @agentrail/web build`         | Pass                                                                        |

Manual verification of the slice against the Compose stack: job created (`201`), executed by the
worker, `COMPLETED` with `attempts = 1`; replayed idempotency key returned `200` with the original
job; the same key with a different body returned `409 idempotency_key_reused`.

No benchmark numbers exist and none may be quoted. Benchmarks are Phase 17.

---

## Known limitations

- No authentication, organisations or tenancy — every endpoint is open (Phase 1).
- The sandbox runs one deterministic no-op task; the synthetic services, metrics, logs, runbooks and
  16 incident families are Phase 2.
- Failed jobs are terminal. No retry budget, no lease expiry, no transactional outbox (Phase 5). The
  recovery sweep is a deliberate stand-in.
- Correlation and trace identifiers propagate, but nothing is exported. No metrics, no dashboards
  (Phase 13).
- MinIO runs in Compose but no service uses object storage yet (Phase 4).
- Idempotency keys are globally unique; they must become organisation-scoped in Phase 1.
- GitHub Actions are pinned to major versions, not immutable SHAs (Phase 14).
- No `LICENSE` file — the licence has not been chosen. **This needs a decision from the owner.**

---

## Unresolved risks

- **Idempotency key scope.** Global uniqueness is wrong once tenants exist; Phase 1 must migrate the
  unique constraint to `(organisation_id, idempotency_key)`.
- **Recovery sweep vs outbox.** The sweep repairs the publish gap but gives a latency floor equal to
  its interval. Phase 5 should replace it rather than tune it.
- **`packages/core-py` owning the `jobs` table.** Justified today because both the API and the worker
  need it and neither can own it. If `core-py` accumulates more domain tables, extract a dedicated
  package instead.
- **E2E depends on Compose in CI.** Reliable locally; watch for flakiness on GitHub-hosted runners and
  add a health-gate step if it appears.

---

## Next tasks (Phase 1)

Only after this pull request is merged:

1. Branch `feat/p01-auth-and-tenancy` from the merged `main`.
2. OAuth browser sign-in; users, organisations, memberships and roles (owner, admin, developer,
   reviewer, viewer).
3. Projects scoped to an organisation.
4. Scoped API keys stored **only** as hashes, with revocation.
5. A central authorisation policy module — not per-route checks.
6. Scope every tenant-owned query, including the existing `jobs` table, and migrate the idempotency
   key constraint to be organisation-scoped.
7. Audit event foundation.
8. Cross-tenant isolation tests: organisation A must not read organisation B, across every surface.
9. Complete UI states for signed-out, loading, empty and permission-denied.
10. Update the threat model (T13, T14, T15) and this checkpoint.

**Exit criteria:** two organisations cannot access each other's data; a revoked key fails; the UI has
complete states; the pull request is green.

---

## Owner actions required

1. Review and merge the Phase 0 pull request.
2. Apply the settings in `docs/BRANCH_PROTECTION.md` once CI has run at least once, so the checks can
   be selected.
3. Decide on a licence and add a `LICENSE` file.
