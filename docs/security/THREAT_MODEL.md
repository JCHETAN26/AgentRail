# Threat model

**Scope: Phase 0.** This document describes the system as built. It is updated in the same pull
request as any change that moves a trust boundary.

Phase 0 has **no authentication and no tenancy**. Everything below assumes a single trusted operator
running the stack locally. The platform is not fit to be exposed to the internet in this state, and
the threats that authentication would address are listed as _not yet mitigated_ rather than omitted.

## Assets

| Asset                                | Why it matters                                                   |
| ------------------------------------ | ---------------------------------------------------------------- |
| Job records in PostgreSQL            | The authoritative record of what the platform did                |
| Side effects performed by the worker | In later phases these become real remediation actions            |
| Infrastructure credentials           | Database, Redis and (later) cloud and model-provider credentials |
| Correlation and trace identifiers    | Support diagnosis; must not leak user content                    |
| The CI/CD pipeline                   | Compromise here compromises every future deployment              |

## Trust boundaries

```text
 [browser] ──1──▶ [Platform API] ──2──▶ [PostgreSQL]
                        │
                        └──3──▶ [Redis] ──4──▶ [Worker] ──5──▶ [CloudOps sandbox]
```

1. **Untrusted → API.** All input is untrusted.
2. **API → database.** Trusted network in Compose; credentialled.
3. **API → Redis.** Delivery only; contents are non-authoritative by design.
4. **Redis → worker.** Message contents are untrusted: a message is a bare identifier and is
   re-validated against the database.
5. **Worker → sandbox.** Internal, but the sandbox treats its input as untrusted and is deterministic
   and side-effect free.

## Threats and current status

| #   | Threat                                            | Status                  | Mechanism                                                                                                                                                     |
| --- | ------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T1  | Oversized request body exhausts memory            | **Mitigated**           | `MaxBodySizeMiddleware` counts streamed chunks rather than trusting `Content-Length`; 64 KiB cap → `413`. Tested.                                             |
| T2  | Malformed or hostile JSON reaches the domain      | **Mitigated**           | Pydantic models with `extra="forbid"`, length bounds on every string, enum-constrained `kind`. Tested.                                                        |
| T3  | Stack traces or driver messages leak to clients   | **Mitigated**           | Every exception is translated to `ProblemDetail`; the traceback goes to the log only. Tested.                                                                 |
| T4  | Secrets leak into logs                            | **Mitigated**           | The JSON formatter redacts any field whose key matches a sensitive marker (`token`, `secret`, `password`, `authorization`, `prompt`, …), recursively. Tested. |
| T5  | Duplicate delivery causes a duplicate side effect | **Mitigated**           | Conditional `UPDATE ... WHERE state = <expected>`; terminal states have no outgoing transitions. Tested with racing workers and tenfold redelivery.           |
| T6  | Client retry creates duplicate work               | **Mitigated**           | `Idempotency-Key` with a request fingerprint; reuse with a different body is rejected. Tested.                                                                |
| T7  | Long-running query pins a connection              | **Mitigated**           | Server-side `statement_timeout` on every connection; bounded pool.                                                                                            |
| T8  | Queue poisoning with a fabricated job id          | **Mitigated**           | The worker re-reads the row; an unknown identifier is dropped. Tested.                                                                                        |
| T9  | Container escape via a root process               | **Mitigated**           | Images run as uid 10001. Asserted in the `containers / build` CI job.                                                                                         |
| T10 | Cross-origin browser access to the API            | **Partially mitigated** | CORS allows one configured origin with an explicit method and header allowlist. There is no authentication behind it yet.                                     |
| T11 | Secrets committed to the repository               | **Mitigated**           | `.env` is git-ignored; `.env.example` contains only local-only values; GitHub secret scanning and CodeQL run on every push.                                   |
| T12 | Vulnerable or hostile dependency                  | **Mitigated**           | Committed lockfiles, `--frozen`/`--frozen-lockfile` installs, Dependabot, and `dependency-review` blocking moderate-and-above advisories.                     |
| T13 | Unauthenticated access to any endpoint            | **Not mitigated**       | No authentication exists. **Phase 1.** Do not expose this deployment.                                                                                         |
| T14 | Cross-tenant data access                          | **Not mitigated**       | No tenancy exists. **Phase 1**, with PostgreSQL RLS as defence in depth in Phase 14.                                                                          |
| T15 | Denial of service by flooding job creation        | **Not mitigated**       | No rate limiting or quota. **Phase 1/14.**                                                                                                                    |
| T16 | Prompt injection through tool output              | **Not applicable yet**  | No model is invoked. Becomes live in Phase 2 with the injection scenarios, and is governed by the policy engine in Phase 10.                                  |
| T17 | Unauthorised high-risk tool execution             | **Not applicable yet**  | The only task is a side-effect-free no-op. Policy and approvals are **Phase 10**.                                                                             |
| T18 | Forged GitHub webhook                             | **Not applicable yet**  | No webhook endpoint exists. Signature verification lands with the integration in **Phase 11**.                                                                |
| T19 | Stored XSS in the console                         | **Low risk today**      | React escapes by default and `dangerouslySetInnerHTML` is not used anywhere. Re-assess when user-supplied trajectory content is rendered in Phase 6.          |
| T20 | Supply-chain compromise of a GitHub Action        | **Partially mitigated** | Actions are pinned to major versions. Immutable SHA pinning is scheduled for **Phase 14**.                                                                    |

## Standing rules

These are review-blocking:

- Never log a raw secret, credential or prompt.
- Never put a real credential in `.env.example` or in a test.
- Never return a stack trace, driver message or internal path to a client.
- Never let Redis hold authoritative state.
- Never accept an unsigned webhook.
- Never store an API key in plaintext.
- Never describe synthetic sandbox output as real telemetry.

## Reporting

Report vulnerabilities privately through a GitHub security advisory. See [`SECURITY.md`](../../SECURITY.md).
