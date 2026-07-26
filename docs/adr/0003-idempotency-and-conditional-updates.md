# ADR 0003 — Idempotency keys at the edge, conditional updates in the core

- **Status:** Accepted
- **Date:** 2026-07-26
- **Phase:** 0

## Context

Two distinct duplication problems exist, and they need different answers.

1. **A client retries a request.** A browser reload or a CI runner retry must not create a second
   job.
2. **The queue delivers a message twice.** Redis delivery is at-least-once, a worker can be killed
   mid-job, and a delayed message can arrive after the work is finished. None of these may cause a
   second execution.

Phase 0 exercises both, and the invariant "forced retries produce zero duplicate side effects" is
one the whole project rests on. It has to be structural, not a convention.

## Decision

### At the edge: idempotency keys

`POST /api/v1/jobs` accepts an optional `Idempotency-Key` header, stored on the row under a unique
constraint. Alongside it the API stores a SHA-256 fingerprint of the canonicalised request body.

- Key not seen before → create the job, return `201`.
- Key seen, fingerprint matches → return the **original** job with `200`, and do not re-publish.
- Key seen, fingerprint differs → `409 idempotency_key_reused`. Silently returning the original job
  would hide a real client bug.

Two concurrent requests carrying the same key race on the unique constraint. The loser catches the
`IntegrityError`, re-reads the winner's row and returns it. The client cannot tell which request won,
which is the point.

### In the core: conditional updates

Every state change is expressed as a conditional update against the expected current state:

```sql
UPDATE jobs SET state = 'RUNNING', attempts = attempts + 1, ...
 WHERE id = :id AND state = 'PENDING'
RETURNING ...
```

Zero rows affected means another actor got there first. That is not an error — the worker logs it and
drops the message. Because `COMPLETED` and `FAILED` have no outgoing transitions at all, a late
duplicate can never reopen finished work.

The domain guard (`assert_transition`) runs before each write. It catches programming errors; the SQL
`WHERE` clause is what actually resolves concurrency. Both are kept: the guard documents the machine,
the database enforces it.

## Alternatives considered

- **A distributed lock (Redis `SETNX`) around each job.** Rejected: introduces lock expiry, clock
  skew and a second store that must be correct for safety. The database row is already the natural
  serialisation point.
- **`SELECT ... FOR UPDATE` then update.** Correct, but two round trips and a held transaction where
  one atomic statement suffices.
- **Optimistic concurrency on the `version` column alone.** The `version` column exists and is
  incremented on every write, but state is the meaningful precondition. Guarding on state expresses
  the intent — "claim this only if nobody has" — directly.
- **Making idempotency keys mandatory.** Rejected for Phase 0: it would force key management on the
  console before there is a user identity to scope keys to. Revisit in Phase 1.

## Consequences

- Duplicate delivery is provably harmless. `TestDuplicateDelivery` submits the same identifier ten
  times and races two workers on one job; exactly one execution occurs.
- Idempotency keys are globally unique, not scoped per tenant. Phase 1 must scope them to an
  organisation when tenancy exists.
- The fingerprint pins the canonical JSON encoding of the request. `test_fingerprint_is_stable_across_processes`
  guards it, because changing the encoding would silently invalidate every stored key.
- There is no retry budget yet: a failed job is terminal. Automatic retry with a budget is Phase 5,
  and it must respect these same guards.
