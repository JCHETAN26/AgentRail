# Security policy

## Project status

AgentRail is at **Phase 1** of an eighteen-phase build. It now has authentication, roles and tenant
isolation, but it still has **no rate limiting, no quotas and no row-level security**, and it has
never been penetration tested. It should not process real data.

The CloudOps sandbox is synthetic. It models no real infrastructure, and it performs no real
remediation.

## Reporting a vulnerability

Report privately through a
[GitHub security advisory](https://github.com/JCHETAN26/AgentRail/security/advisories/new).

**Do not open a public issue.**

Please include what you can of:

- the affected component and commit;
- reproduction steps;
- the impact you believe it has;
- any correlation id from a request that demonstrates it.

Expect an acknowledgement within a few days. Fixes ship through the normal pull-request process,
and the advisory is published once a fix is on `main`.

## Supported versions

Only `main` is supported. There are no releases yet.

## What is in scope

- The platform API, worker, sandbox and web console in this repository.
- The CI/CD workflows in `.github/workflows/`.
- The container images built from `infra/compose/Dockerfile.python`.

## What is out of scope

The following are **known and documented** gaps, tracked as build phases rather than vulnerabilities:

- No rate limiting or quotas — an authenticated caller can create unbounded work (Phase 14).
- No PostgreSQL row-level security beneath the application-level tenant scoping (Phase 14).
- No automatic API-key rotation and no anomaly detection on key use (Phase 14).
- Audit events are append-only in application code, but nothing at the database level enforces it,
  and there is no retention policy yet (Phase 13).
- No policy engine or human approval for tool execution (Phase 10).
- No webhook signature verification, because no webhook endpoint exists (Phase 11).
- GitHub Actions pinned to major versions rather than immutable SHAs (Phase 14).

`docs/security/THREAT_MODEL.md` lists these alongside what _is_ mitigated today, with the mechanism
and the test that covers it.

## Standing commitments

- Secrets are never logged: the JSON formatter redacts sensitive keys before serialisation.
- `.env.example` contains only values that are valid on a local machine and nowhere else.
- API keys are stored only as one-way digests, scoped to one organisation, bounded by a role and
  optional scopes, and revocable immediately.
- Session tokens are opaque, stored only as digests, `HttpOnly`, `SameSite=Lax`, `Secure` when
  deployed, and revoked server-side on sign-out.
- Passwordless development sign-in is structurally unavailable in deployed environments.
- Container images run as an unprivileged user, asserted in CI.
- Dependencies are installed from committed lockfiles with frozen installs, so the dependency set is
  reproducible, and Dependabot raises version updates weekly.
- **The `dependency-review` gate is not yet in force.** It warns and skips while the repository's
  dependency graph is disabled, so it is excluded from the required status checks — a change
  introducing a moderate-or-higher advisory can currently merge. This is tracked as T12 in
  `docs/security/THREAT_MODEL.md` and closes as soon as the dependency graph is enabled.
