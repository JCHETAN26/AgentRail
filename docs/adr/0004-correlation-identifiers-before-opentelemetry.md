# ADR 0004 — Propagate correlation and trace identifiers now, export spans in Phase 13

- **Status:** Accepted
- **Date:** 2026-07-26
- **Phase:** 0

## Context

The build plan places full observability — OpenTelemetry SDKs, a Collector, Prometheus metrics,
Grafana dashboards, Tempo traces — in Phase 13. It also requires from the outset that every
user-visible error carries a correlation id, and that context propagates across the web, API, queue,
worker and tool boundaries.

Those two requirements pull in opposite directions. Installing the whole OpenTelemetry stack in
Phase 0 is exactly the "technology showcase" the system prompt warns against: exporters, a Collector
and dashboards with four endpoints to observe. But deferring identifier propagation entirely means
Phase 13 becomes a retrofit that touches every request path, every log line and every stored row.

## Decision

Split the two concerns.

**Now (Phase 0):** identifier plumbing, hand-rolled and small.

- `CorrelationContext` carries `correlation_id`, `trace_id`, `span_id` and the sampled flag.
- `CorrelationMiddleware` parses the inbound W3C `traceparent`, continues the trace (starting a fresh
  span for the hop), generates whatever is missing, binds the context to a `contextvar`, and echoes
  both headers on the response.
- A malformed inbound `traceparent` starts a new trace rather than failing the request, as the W3C
  specification requires.
- The context is forwarded on outbound calls (`SandboxClient`) and **persisted on the job row**, so a
  job's origin is recoverable long after the request has gone.
- The JSON log formatter attaches the identifiers to every line automatically.

**Later (Phase 13):** the OpenTelemetry SDK, Collector, metrics and dashboards, consuming the context
that already exists.

## Why not use the OpenTelemetry SDK for propagation only

It was the closest alternative: `opentelemetry-api` plus a propagator, no exporter. Rejected for two
reasons. First, a partially wired SDK invites the belief that traces are being collected when nothing
is exported — the project's honesty rules make that unacceptable. Second, the propagation logic is
roughly a hundred lines and fully unit-tested, whereas the SDK brings a configuration surface that
would need to be revisited in Phase 13 anyway.

The identifiers are W3C-format on purpose, so Phase 13 can adopt the SDK without changing any stored
value or wire format.

## Alternatives considered

- **Full OpenTelemetry now.** Rejected: out of phase, and it would mean either running a Collector in
  CI or shipping an exporter that silently drops spans.
- **A correlation id only, no trace context.** Simpler, but `trace_id`/`span_id` are what let Phase 13
  join a stored job to a distributed trace. Storing them costs 48 bytes per row.
- **Starlette's `BaseHTTPMiddleware`.** Rejected on a technical point: it runs the downstream app in a
  separate task, so `contextvars` written by the endpoint are lost. The middleware is raw ASGI
  instead.

## Consequences

- Every error response and every log line carries a correlation id today.
- `jobs.correlation_id` and `jobs.trace_id` are populated from Phase 0, so no backfill is needed.
- Until Phase 13 there is no span export, no latency histogram and no dashboard. This is stated in the
  README's limitations so nobody infers otherwise.
- `packages/core-py/src/agentrail_core/correlation.py` is code the project maintains itself, and it
  should be deleted in favour of the SDK's propagator in Phase 13.
