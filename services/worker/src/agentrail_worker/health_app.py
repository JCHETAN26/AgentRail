"""The worker's own HTTP health surface.

A background worker still needs probes: an orchestrator has no other way to tell
a hung consumer from a healthy idle one.
"""

from __future__ import annotations

from fastapi import FastAPI, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from agentrail_core.db import check_database
from agentrail_core.health import HealthResponse, ReadinessResponse, evaluate_readiness
from agentrail_core.queue import check_redis
from agentrail_worker import __version__
from agentrail_worker.sandbox_client import SandboxClient


def create_health_app(
    *,
    service_name: str,
    engine: AsyncEngine,
    redis_client: Redis,
    sandbox: SandboxClient,
) -> FastAPI:
    app = FastAPI(title="AgentRail Worker", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/healthz", response_model=HealthResponse, tags=["health"])
    async def healthz() -> HealthResponse:
        return HealthResponse(service=service_name, version=__version__)

    @app.get("/readyz", response_model=ReadinessResponse, tags=["health"])
    async def readyz(response: Response) -> ReadinessResponse:
        report = await evaluate_readiness(
            service=service_name,
            version=__version__,
            checks={
                "postgresql": lambda: check_database(engine),
                "redis": lambda: check_redis(redis_client),
                "cloudops_sandbox": sandbox.ping,
            },
        )
        if report.status == "not_ready":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return report

    return app
