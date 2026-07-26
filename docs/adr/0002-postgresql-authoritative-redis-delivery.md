# ADR 0002 — PostgreSQL is authoritative; Redis carries delivery only

- **Status:** Accepted
- **Date:** 2026-07-26
- **Phase:** 0

## Context

The platform needs to hand work from the API to a worker. Redis is already required for leases and
rate limiting in later phases, and Redis lists are the shortest path to a working queue. The
temptation is to let the queue _be_ the state: push a job payload, pop it, done.

That fails the project's own standard. A Redis restart without persistence loses queued work
silently. Worse, "is this job finished?" becomes a question with two possible answers depending on
which store you ask.

## Decision

PostgreSQL holds all job state. Redis holds nothing but a job identifier, and only as a wake-up
signal.

Concretely:

1. `POST /api/v1/jobs` inserts the row and **commits**.
2. Only then is the identifier `RPUSH`-ed to Redis.
3. The worker `BLPOP`s an identifier and re-reads the row from PostgreSQL before doing anything.
4. Every state change is a conditional `UPDATE ... WHERE state = <expected>` against PostgreSQL.

A worker that receives an identifier for a row that does not exist drops the message: the identifier
can never become valid.

The reverse ordering — publish then commit — was rejected because it allows a worker to dequeue an
identifier for a row that has not been committed, or that a rollback means will never exist.

## The gap this creates, and how it is closed

Committing first opens a window: the row exists but the publish failed, so no worker is ever woken.
The worker therefore runs a periodic sweep that re-publishes jobs left in `PENDING` past a
configurable age (`AGENTRAIL_STALE_PENDING_SECONDS`, default 30s). Re-publishing an already-claimed
job is harmless because the claim is a conditional update.

This is a deliberate stand-in for a transactional outbox, which arrives in Phase 5 and will replace
the sweep. The sweep is honest about what it is: a repair loop, not a delivery guarantee.

## Alternatives considered

- **Redis Streams with consumer groups.** More capable than a list, and likely the Phase 5 choice for
  leases. Rejected now because it does not change the authority question and would add operational
  surface for no Phase 0 benefit.
- **`LISTEN`/`NOTIFY` on PostgreSQL, no Redis.** Attractive — one fewer store — but notifications are
  lost if no listener is connected, and Redis is required for later phases regardless.
- **A transactional outbox now.** The correct end state, and it is scheduled. Building it in Phase 0
  would mean a table, a relay process and its failure modes before there is any consumer that needs
  the guarantee.

## Consequences

- Losing the Redis database delays jobs; it does not lose them. This is verified by
  `TestRecoverySweep` in `services/worker/tests/test_worker_loop.py`.
- Every consumer must be idempotent, because delivery is at-least-once. See
  [ADR 0003](0003-idempotency-and-conditional-updates.md).
- There is a latency floor equal to the sweep interval for jobs that hit the publish gap.
- Redis must never be given authoritative data. This is a standing review rule, restated in
  `packages/core-py/src/agentrail_core/queue.py`.
