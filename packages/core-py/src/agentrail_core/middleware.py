"""ASGI middleware shared by every AgentRail HTTP service.

Implemented as raw ASGI rather than Starlette's ``BaseHTTPMiddleware`` so that
the correlation context is bound in the same task that runs the endpoint —
``BaseHTTPMiddleware`` runs the downstream app in a separate task, which loses
``contextvars`` written by the endpoint.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.exceptions import HTTPException

from agentrail_core.correlation import (
    CORRELATION_HEADER,
    TRACEPARENT_HEADER,
    context_from_headers,
    correlation_scope,
)
from agentrail_core.logging import get_logger

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_logger = get_logger(__name__)

#: Paths excluded from access logging to keep probe traffic out of the log
#: volume. They are still traced.
_QUIET_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})


class CorrelationMiddleware:
    """Bind correlation and trace context for the lifetime of a request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        context = context_from_headers(headers)
        scope["correlation_context"] = context

        started = time.perf_counter()
        status_holder: dict[str, int] = {}

        async def send_with_context(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
                raw_headers = list(message.get("headers", []))
                raw_headers.append(
                    (CORRELATION_HEADER.encode("latin-1"), context.correlation_id.encode("latin-1"))
                )
                raw_headers.append(
                    (
                        TRACEPARENT_HEADER.encode("latin-1"),
                        context.to_traceparent().encode("latin-1"),
                    )
                )
                message["headers"] = raw_headers
            await send(message)

        with correlation_scope(context):
            try:
                await self.app(scope, receive, send_with_context)
            finally:
                path = scope.get("path", "")
                if path not in _QUIET_PATHS:
                    _logger.info(
                        "http_request",
                        extra={
                            "http_method": scope.get("method"),
                            "http_path": path,
                            "http_status": status_holder.get("status"),
                            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                        },
                    )


class MaxBodySizeMiddleware:
    """Reject request bodies larger than ``max_bytes``.

    Enforced by counting streamed chunks rather than trusting ``Content-Length``,
    so a lying or absent header cannot bypass the limit.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge(self.max_bytes)
            return message

        await self.app(scope, limited_receive, send)


class BodyTooLarge(HTTPException):
    """Raised by :class:`MaxBodySizeMiddleware` when the limit is exceeded.

    Subclasses Starlette's ``HTTPException`` deliberately: FastAPI wraps any
    *other* exception raised while reading a request body in a generic
    ``400 There was an error parsing the body``, which would hide the real
    reason. ``HTTPException`` subclasses are re-raised untouched and reach the
    application's registered handler.
    """

    def __init__(self, max_bytes: int) -> None:
        super().__init__(status_code=413, detail=f"Request body exceeds {max_bytes} bytes")
        self.max_bytes = max_bytes
