# Checkpoint

Operational handoff between sessions. This file is the first thing to read when resuming work.

---

## Current state

|                |                                                                                                       |
| -------------- | ----------------------------------------------------------------------------------------------------- |
| **Phase**      | 0 — Repository, product contract and guardrails                                                       |
| **Status**     | **Merged.** PR [#1](https://github.com/JCHETAN26/AgentRail/pull/1), rebased onto `main` on 2026-07-26 |
| **Next phase** | 1 — Authentication, organisations and tenancy. Ready to start.                                        |
| **Guardrails** | Branch protection **live** on `main`; direct pushes rejected                                          |

`main` was bootstrapped with a `.gitignore` and `README.md` only, because the GitHub repository was
empty and a pull request needs a base branch to exist. Per the repository owner's instruction, the
planning documents (`BUILDPLAN.md`, `SYSTEM_PROMPT.md`, `CLAUDE_CODE_MASTER_PROMPT.md`) are
git-ignored and are **not** published to the remote.

### Post-merge housekeeping (2026-07-26)

- **Branch protection applied and verified** — see `docs/BRANCH_PROTECTION.md` for the exact
  configuration, the three documented deviations from the build plan, and the verification output.
- **Dependabot's opening wave of 17 pull requests triaged** — 13 merged, 4 closed. Closed with
  reasons: `#8` Python 3.14 base image (violates the `requires-python <3.13` pin; all three container
  builds failed), `#14` redis 5→8 (three majors; `python` and `integration` failed), `#13` React
  group and `#15` the 9-package tooling group (both red, and grouped updates cannot be taken
  partially). Each will be re-raised by Dependabot and deserves its own branch.
- **MIT licence added.**
- **One blemish in the history:** `565bc7c` is an empty commit titled
  `test: should be rejected by branch protection`. It was pushed directly to `main` while verifying
  the protection rules, at a point when "Include administrators" was off and the push was therefore
  allowed rather than rejected. The setting is now on and a repeat is impossible. The commit is empty
  — no file changed — and removing it would require a force-push to a shared branch, which the
  project rules forbid, so it stays as a documented artefact.

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
- `dependency-review` fails on every pull request and is therefore not a required check. It is
  blocked on a browser-only repository setting — see "Owner actions required".
- Branch protection requires **0** approvals, because a single maintainer cannot approve their own
  pull request. Raise it to 1 when a second maintainer joins.

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

Phase 0 is merged, so Phase 1 may begin:

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

**Exactly one item remains, and it cannot be automated.**

1. **Enable the dependency graph** at
   [`Settings → Code security and analysis`](https://github.com/JCHETAN26/AgentRail/settings/security_analysis).

   There is no REST API for this: `PATCH /repos/{owner}/{repo}` accepts the `secret_scanning*` and
   `dependabot_security_updates` fields under `security_and_analysis`, but not `dependency_graph`.
   It is a browser-only toggle.

   Three things unblock at once when it is on — `dependency-review` starts passing, Dependabot
   security alerts and updates become available, and `dependency-review` can be added to the required
   status checks in `docs/BRANCH_PROTECTION.md`.

   Verify with `gh api repos/JCHETAN26/AgentRail/dependency-graph/sbom` — a `404` means still off.

Completed on 2026-07-26: Phase 0 merged, branch protection applied and verified, Dependabot's opening
wave triaged, MIT licence added.
