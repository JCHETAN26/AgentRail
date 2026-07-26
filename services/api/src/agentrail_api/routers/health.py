"""Liveness and readiness endpoints.

``/healthz`` deliberately touches nothing: it answers whether the process is
running. ``/readyz`` checks PostgreSQL and Redis and returns 503 when either is
unusable, so an orchestrator removes the instance from rotation instead of
restarting a process that is working correctly.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from agentrail_api import __version__
from agentrail_api.dependencies import RedisDep, SettingsDep
from agentrail_core.db import check_database
from agentrail_core.health import HealthResponse, ReadinessResponse, evaluate_readiness
from agentrail_core.queue import check_redis

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse, summary="Liveness probe")
async def healthz(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(service=settings.service_name, version=__version__)


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable."}},
)
async def readyz(
    request: Request, response: Response, settings: SettingsDep, client: RedisDep
) -> ReadinessResponse:
    async def database_check() -> None:
        await check_database(request.app.state.engine)

    async def redis_check() -> None:
        await check_redis(client)

    report = await evaluate_readiness(
        service=settings.service_name,
        version=__version__,
        checks={"postgresql": database_check, "redis": redis_check},
    )
    if report.status == "not_ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
