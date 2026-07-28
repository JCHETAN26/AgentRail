# ADR 0006 — Delegated authentication, opaque sessions, and tenancy enforced at one function

- **Status:** Accepted
- **Date:** 2026-07-26
- **Phase:** 1

## Context

Phase 1 has to answer three questions at once, and answering them separately would produce three
inconsistent mechanisms:

1. **Who is this?** A human in a browser, or a CI job with a credential.
2. **Which tenant are they acting in?** Everything is now owned by an organisation.
3. **May they do this?** Roles exist, and the build plan requires "central policy functions", not
   per-route checks.

There is also a constraint that shapes everything: **the deterministic demo must work without any
paid or configured credential.** Requiring a GitHub OAuth application in order to run the test suite
would break the property Phase 0 was built to protect.

## Decision

### Authentication is delegated, and pluggable

No password is ever stored. Two providers implement one protocol:

- `DevAuthProvider` — the "code" is an email address; the same address always resolves to the same
  account. Deterministic, no network, no configuration. This is what local development, CI and the
  public demo use.
- `GitHubOAuthProvider` — a real OAuth exchange with a `state` parameter checked against an HttpOnly
  cookie, used in staging and production.

The dev provider is **structurally unavailable** once deployed: `ApiSettings.dev_auth_enabled` is
false when the environment is `staging` or `production`, the route reports 404-shaped rejection
rather than advertising itself, and `ApiSettings` refuses to construct at all in a deployed
environment without GitHub credentials. Passwordless sign-in reaching production would be the worst
failure this phase could produce, so it is prevented by three independent mechanisms rather than by a
convention.

Users are matched on `(provider, subject)`, never on email. Providers let people change their email;
matching on it would let whoever next owns an address inherit the original account. GitHub emails are
additionally required to be `primary` **and** `verified`.

### Sessions are opaque and server-side

The session cookie holds a 256-bit random token. Only its one-way digest is stored. The cookie is
`HttpOnly` (so XSS cannot read it), `SameSite=Lax` (so a cross-site POST cannot carry it), and
`Secure` in deployed environments.

A JWT was the alternative. Rejected because sign-out must be _real_: a stateless token stays valid
until it expires, so "sign out" would only clear the browser's copy while a captured token kept
working. A server-side row can be revoked, and `test_sign_out_revokes_the_session_server_side`
replays a captured token to prove it.

PBKDF2-HMAC-SHA256 keeps persisted bearer tokens non-replayable while satisfying the same scanner
rule set that protects human-password code paths. These are machine-generated 256-bit secrets, not
user-chosen passwords, so the KDF is a defence-in-depth and review-signal choice rather than the
primary source of strength.

### API keys are hashed, split, and doubly bounded

Format: `ar_<key_id>_<secret>`. `key_id` is public and indexed, so verification is a single indexed
lookup rather than a table scan; only the secret's digest is stored, and comparison uses
`hmac.compare_digest`. The full token exists exactly once, in the creation response.

A key carries both a **role** and an optional **scope** list, and its effective permissions are the
_intersection_. A leaked key is therefore bounded twice, and a key can never out-rank the principal
that minted it.

### Authorisation is one function

`agentrail_core.identity.roles.authorize(principal, permission, organisation_id=...)` is the only
place an access decision is made. It is pure — no database, no HTTP — so the entire matrix is
exhaustively unit-tested, including "no role may act in another organisation" across every role and
every permission.

Both credential kinds normalise to the same `Principal`, so no route knows or cares which was used.

### Denials are indistinguishable

`authorize` checks tenancy **first**, and both failures raise the same error, which the API renders
as an identical `403 forbidden` with an identical message. A caller cannot tell "that organisation is
not yours" from "you lack that permission" from "that does not exist".

This is why fetching another tenant's job returns **403, not 404**: a 404 would confirm which
identifiers are real, turning the API into an enumeration oracle. `test_a_nonexistent_organisation_is_indistinguishable_from_someone_elses`
asserts that the status, code and message all match.

### Idempotency keys became project-scoped

Phase 0 made `jobs.idempotency_key` globally unique. Under tenancy that is a leak _and_ a bug: two
tenants could collide on an ordinary key like `nightly-run`, and the resulting 409 would reveal that
somebody else had used it. The constraint is now `(project_id, idempotency_key)`.

Existing Phase 0 jobs are adopted into a deterministic Legacy organisation and project during
migration. Because Phase 0 had no users, the Legacy organisation cannot be mapped to a historical
owner. To avoid orphaning migrated jobs forever, the first authenticated user after upgrade receives
owner membership if, and only if, the Legacy organisation exists and still has no members.

## Alternatives considered

- **JWT sessions.** Rejected — see above; revocation is the deciding factor.
- **Per-route permission checks.** Rejected: the build plan calls for central policy, and scattered
  checks are exactly how one endpoint ends up missing one.
- **Returning 404 for another tenant's resources.** Rejected as an enumeration oracle.
- **PostgreSQL row-level security now.** Deferred to Phase 15, as the build plan schedules it.
  Application-level scoping comes first and is tested first; RLS is defence in depth, not a
  substitute for the tests.
- **Mandatory API-key scopes.** Rejected for now: an empty scope list meaning "the role's full
  permissions" keeps the common case simple, and the role is still a hard ceiling.

## Consequences

- The whole test suite and the end-to-end flow run with no OAuth application configured.
- Jobs moved under `/api/v1/projects/{project_id}/jobs`; there is no unscoped job listing, and
  `list_jobs` takes `project_id` as a required keyword argument so an unscoped query is not
  expressible.
- `packages/core-py` now owns identity tables as well as the job table, on the same reasoning as
  ADR 0001: the API writes them and the worker reads through them, so neither service can own them.
- Threat-model items T13 (unauthenticated access) and T14 (cross-tenant access) close, and T10
  (cross-origin access) closes but now depends on `allow_credentials`, which makes the explicit
  origin list load-bearing — a wildcard origin would be both rejected by the browser and a real
  vulnerability. T21–T27 are added for the new attack surface. **T15 (denial of service) does not
  close:** identifying callers is a precondition for limiting them, not a limit.
- Organisation-level rate limiting and quotas are still absent (Phase 15), so an authenticated user
  can still create unbounded work.
