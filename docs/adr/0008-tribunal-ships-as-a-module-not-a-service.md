# ADR 0008 — The Tribunal ships as a domain module, not a separate service

- **Status:** Accepted
- **Date:** 2026-07-29
- **Phase:** 8

## Context

The Phase 0 checklist enumerates `services/tribunal` among the monorepo's services. No such service
exists. The Multi-Agent Safety Tribunal is instead implemented as
`packages/core-py/src/agentrail_core/tribunal.py` — the deterministic six-role decision logic and its
persistence models — with API use cases in `services/api/src/agentrail_api/tribunal/` and routes in
`routers/tribunal.py`. The worker creates Tribunal sessions inline during run aggregation.

That divergence needed resolving in one direction or the other before the remaining Phase 8 items
could be called complete against a checklist that describes a service which was never built.

## Decision

The Tribunal remains a domain module plus API routes. `services/tribunal` will not be created, and
the Phase 0 checklist item is amended to match.

`BUILDPLAN.md:347` already contemplates this: it specifies a "**LangGraph subgraph** within the worker
or dedicated tribunal worker". Both were permitted; the in-worker form is what exists.

## Rationale

- **The Tribunal is not independently scalable in a way that matters.** It runs once per evaluation
  run, immediately after aggregation, using evidence already loaded in that transaction. A separate
  service would fetch the same rows back over the network to do the same work.
- **Determinism is easier to guarantee in-process.** The recorded model client that keeps CI and the
  demo reproducible is a constructor argument. Across a service boundary it becomes a deployment
  concern, and "which build of the tribunal service produced this verdict" becomes a question the
  verdict digest has to answer.
- **Transactional integrity.** `create_or_get_tribunal_session` is idempotent within the run's
  transaction, so a verdict and the run state it gates commit together. Split across a service, that
  becomes a distributed transaction or an eventually-consistent gate — and the gate's whole purpose is
  to block a release, which is not a thing to be eventually consistent about.
- **Cost.** A service means another image, another container scan job, another health endpoint,
  another deployment target and another failure mode, for no capability the module lacks.

## Alternatives considered

- **Extract `services/tribunal` as specified.** Rejected for the reasons above. It would be the right
  call if Tribunal debate became long-running or model-bound enough to need independent scaling and
  its own rate limits — see below.
- **Extract only the model-backed debate path.** The deterministic path stays in-process and only
  live-provider debate moves out. Genuinely attractive once live debate is common, because provider
  latency and rate limits are the real reason to isolate it. Deferred rather than rejected: today
  model-backed mode is opt-in per suite and the recorded client covers CI.
- **Leave the checklist item unchecked forever.** Rejected as dishonest bookkeeping — it implies
  outstanding work that nobody intends to do.

## Consequences

- The Phase 0 checklist item is reworded to name the Tribunal module rather than a service, with a
  pointer to this ADR.
- Tribunal logic is subject to the same `agentrail-core` constraint as everything else in the domain:
  it must not depend on an agent framework. See
  [ADR 0007](0007-langgraph-as-an-execution-adapter.md).
- If Tribunal debate later needs independent scaling, the seam to cut along is the model client, not
  the decision logic — the decision logic is pure and belongs with the domain either way.
