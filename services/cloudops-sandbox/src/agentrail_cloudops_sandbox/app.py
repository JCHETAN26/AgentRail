"""HTTP surface for the deterministic sandbox."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware import Middleware

from agentrail_cloudops_sandbox import __version__
from agentrail_cloudops_sandbox.tasks import execute_noop
from agentrail_core.correlation import CorrelationContext, new_correlation_id
from agentrail_core.errors import ErrorCode, ProblemDetail
from agentrail_core.health import HealthResponse, ReadinessResponse
from agentrail_core.logging import configure_logging, get_logger
from agentrail_core.middleware import CorrelationMiddleware
from agentrail_core.settings import CoreSettings

logger = get_logger(__name__)


class SandboxSettings(CoreSettings):
    service_name: str = "agentrail-cloudops-sandbox"


class NoopTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=500)


class NoopTaskResponse(BaseModel):
    echo: str
    digest: str = Field(description="First 16 hex characters of SHA-256(message).")
    sandbox_version: str


def create_app(settings: SandboxSettings | None = None) -> FastAPI:
    resolved = settings or SandboxSettings()
    configure_logging(
        service=resolved.service_name,
        environment=resolved.environment.value,
        level=resolved.log_level,
    )

    app = FastAPI(
        title="AgentRail CloudOps Sandbox",
        version=__version__,
        description=(
            "Synthetic, deterministic CloudOps environment. All data is fabricated for "
            "evaluation purposes and represents no real infrastructure."
        ),
        middleware=[Middleware(CorrelationMiddleware)],
    )
    app.state.settings = resolved

    @app.get("/healthz", response_model=HealthResponse, tags=["health"])
    async def healthz() -> HealthResponse:
        return HealthResponse(service=resolved.service_name, version=__version__)

    @app.get("/readyz", response_model=ReadinessResponse, tags=["health"])
    async def readyz() -> ReadinessResponse:
        # The sandbox is intentionally dependency-free: it holds its state in
        # process, so readiness is equivalent to liveness.
        return ReadinessResponse(
            status="ready", service=resolved.service_name, version=__version__, dependencies=[]
        )

    @app.post("/v1/tasks/noop", response_model=NoopTaskResponse, tags=["tasks"])
    async def noop_task(request: NoopTaskRequest) -> NoopTaskResponse:
        result = execute_noop(request.message)
        logger.info("sandbox_noop_executed", extra={"digest": result["digest"]})
        return NoopTaskResponse(**result)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        context = request.scope.get("correlation_context")
        correlation_id = (
            context.correlation_id
            if isinstance(context, CorrelationContext)
            else new_correlation_id()
        )
        logger.exception("unhandled_error", extra={"error_type": type(exc).__name__})
        problem = ProblemDetail(
            code=ErrorCode.INTERNAL_ERROR,
            message="An unexpected error occurred in the sandbox.",
            correlation_id=correlation_id,
        )
        return JSONResponse(status_code=500, content=problem.model_dump(mode="json"))

    return app
