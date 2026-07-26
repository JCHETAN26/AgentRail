"""Request correlation and W3C trace-context propagation.

Every unit of work in AgentRail carries:

* a ``correlation_id`` — a human-quotable identifier surfaced in the UI and in
  every error response, stable across the whole web → API → queue → worker path;
* a ``trace_id`` / ``span_id`` pair in W3C ``traceparent`` form, so that the
  OpenTelemetry exporters introduced in a later phase have real context to
  attach to rather than a retrofit.

The full OpenTelemetry SDK is intentionally *not* wired up in Phase 0; this
module implements only the identifier plumbing that the SDK will later consume.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

_TRACEPARENT_PATTERN = re.compile(
    r"^(?P<version>[0-9a-f]{2})-"
    r"(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-"
    r"(?P<flags>[0-9a-f]{2})$"
)
_INVALID_TRACE_ID = "0" * 32
_INVALID_SPAN_ID = "0" * 16

CORRELATION_HEADER = "x-correlation-id"
TRACEPARENT_HEADER = "traceparent"


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """Identifiers describing the in-flight unit of work."""

    correlation_id: str
    trace_id: str
    span_id: str
    sampled: bool = True

    def to_traceparent(self) -> str:
        return render_traceparent(self.trace_id, self.span_id, sampled=self.sampled)

    def to_headers(self) -> dict[str, str]:
        """Headers to forward on an outbound call so context survives the hop."""
        return {
            CORRELATION_HEADER: self.correlation_id,
            TRACEPARENT_HEADER: self.to_traceparent(),
        }

    def to_log_fields(self) -> dict[str, str]:
        return {
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }


_context: ContextVar[CorrelationContext | None] = ContextVar(
    "agentrail_correlation_context", default=None
)


def new_correlation_id() -> str:
    """Return a fresh correlation identifier."""
    return f"cid_{os.urandom(12).hex()}"


def new_trace_id() -> str:
    return os.urandom(16).hex()


def new_span_id() -> str:
    return os.urandom(8).hex()


def parse_traceparent(header: str | None) -> tuple[str, str, bool] | None:
    """Parse a W3C ``traceparent`` header.

    Returns ``(trace_id, span_id, sampled)`` or ``None`` when the header is
    absent or malformed. Malformed inbound trace context is never fatal — a new
    trace is started instead, which is what the W3C specification requires.
    """
    if not header:
        return None
    match = _TRACEPARENT_PATTERN.match(header.strip().lower())
    if match is None:
        return None
    trace_id = match.group("trace_id")
    span_id = match.group("span_id")
    if trace_id == _INVALID_TRACE_ID or span_id == _INVALID_SPAN_ID:
        return None
    sampled = bool(int(match.group("flags"), 16) & 0x01)
    return trace_id, span_id, sampled


def render_traceparent(trace_id: str, span_id: str, *, sampled: bool = True) -> str:
    return f"00-{trace_id}-{span_id}-{'01' if sampled else '00'}"


def context_from_headers(headers: dict[str, str] | None = None) -> CorrelationContext:
    """Build a context from inbound headers, generating anything missing."""
    lowered = {key.lower(): value for key, value in (headers or {}).items()}
    parsed = parse_traceparent(lowered.get(TRACEPARENT_HEADER))
    if parsed is None:
        trace_id, sampled = new_trace_id(), True
    else:
        trace_id, _parent_span_id, sampled = parsed
    correlation_id = lowered.get(CORRELATION_HEADER) or new_correlation_id()
    return CorrelationContext(
        correlation_id=correlation_id[:128],
        trace_id=trace_id,
        span_id=new_span_id(),
        sampled=sampled,
    )


def current_context() -> CorrelationContext | None:
    """Return the context bound to the current task, if any."""
    return _context.get()


def bind_context(context: CorrelationContext) -> Token[CorrelationContext | None]:
    return _context.set(context)


def reset_context(token: Token[CorrelationContext | None]) -> None:
    _context.reset(token)


@contextmanager
def correlation_scope(context: CorrelationContext) -> Iterator[CorrelationContext]:
    """Bind ``context`` for the duration of the block."""
    token = bind_context(context)
    try:
        yield context
    finally:
        reset_context(token)
