# Security policy

## Project status

AgentRail is at **Phase 0** of an eighteen-phase build. It has **no authentication, no authorisation
and no tenant isolation**. It is not fit to be exposed to a network beyond your own machine, and it
should not process real data.

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

- No authentication or authorisation on any endpoint (Phase 1).
- No tenant isolation (Phase 1; PostgreSQL RLS in Phase 14).
- No rate limiting or quotas (Phase 1 / 14).
- No policy engine or human approval for tool execution (Phase 10).
- No webhook signature verification, because no webhook endpoint exists (Phase 11).
- GitHub Actions pinned to major versions rather than immutable SHAs (Phase 14).

`docs/security/THREAT_MODEL.md` lists these alongside what _is_ mitigated today, with the mechanism
and the test that covers it.

## Standing commitments

- Secrets are never logged: the JSON formatter redacts sensitive keys before serialisation.
- `.env.example` contains only values that are valid on a local machine and nowhere else.
- API keys, when they arrive in Phase 1, will be stored only as hashes.
- Container images run as an unprivileged user, asserted in CI.
- Dependencies are installed from committed lockfiles, reviewed by Dependabot, and blocked on
  moderate-or-higher advisories by `dependency-review`.
