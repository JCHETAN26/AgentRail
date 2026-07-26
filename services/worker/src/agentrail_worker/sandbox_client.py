"""HTTP client for the CloudOps sandbox.

Correlation and trace headers are forwarded on every call so a sandbox log line
can be joined to the API request that ultimately caused it.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import httpx

from agentrail_core.correlation import CorrelationContext
from agentrail_core.errors import DependencyUnavailableError


class SandboxClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """``transport`` exists so tests can talk to the real sandbox app
        in-process over ASGI instead of a socket."""
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers={"user-agent": "agentrail-worker"},
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> None:
        """Readiness check used by the worker's own ``/readyz``."""
        try:
            response = await self._client.get("/healthz")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(
                "CloudOps sandbox is not reachable", details={"dependency": "cloudops_sandbox"}
            ) from exc

    async def execute_noop(self, message: str, context: CorrelationContext) -> dict[str, Any]:
        try:
            response = await self._client.post(
                "/v1/tasks/noop",
                json={"message": message},
                headers=context.to_headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(
                "CloudOps sandbox call failed",
                details={"dependency": "cloudops_sandbox", "reason": type(exc).__name__},
            ) from exc
        payload: dict[str, Any] = response.json()
        return payload
