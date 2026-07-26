"""Liveness and readiness primitives.

``/healthz`` answers "is this process alive?" and must never touch a dependency —
otherwise a Redis blip restarts every healthy container.

``/readyz`` answers "should this process receive traffic?" and therefore *does*
check dependencies, reporting each one individually so an operator can see which
is at fault.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, Field

DependencyCheck = Callable[[], Awaitable[None]]


class DependencyStatus(BaseModel):
    name: str
    status: Literal["up", "down"]
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    service: str
    version: str
    dependencies: list[DependencyStatus] = Field(default_factory=list)


async def evaluate_readiness(
    *,
    service: str,
    version: str,
    checks: dict[str, DependencyCheck],
    timeout_seconds: float = 5.0,
) -> ReadinessResponse:
    """Run every dependency check concurrently and summarise the result."""

    async def run(name: str, check: DependencyCheck) -> DependencyStatus:
        try:
            await asyncio.wait_for(check(), timeout=timeout_seconds)
        except TimeoutError:
            return DependencyStatus(name=name, status="down", detail="timeout")
        except Exception as exc:  # noqa: BLE001 - readiness must never raise
            return DependencyStatus(name=name, status="down", detail=type(exc).__name__)
        return DependencyStatus(name=name, status="up")

    results = await asyncio.gather(*(run(name, check) for name, check in checks.items()))
    ordered = sorted(results, key=lambda item: item.name)
    ready = all(item.status == "up" for item in ordered)
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        service=service,
        version=version,
        dependencies=ordered,
    )
