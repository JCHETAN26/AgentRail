"""FastAPI application factory.

Responsibilities kept here and nowhere else: process lifecycle (engine and Redis
client creation and disposal), middleware order, and the translation of every
exception into the platform :class:`ProblemDetail` contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from agentrail_api import __version__
from agentrail_api.routers import auth, health, jobs, organisations
from agentrail_api.settings import ApiSettings, api_settings
from agentrail_core.correlation import CorrelationContext, new_correlation_id
from agentrail_core.db import create_database_engine, create_session_factory
from agentrail_core.errors import ErrorCode, ForbiddenError, PlatformError, ProblemDetail
from agentrail_core.identity import AuthorisationError
from agentrail_core.logging import configure_logging, get_logger
from agentrail_core.middleware import BodyTooLarge, CorrelationMiddleware, MaxBodySizeMiddleware
from agentrail_core.queue import create_redis_client

logger = get_logger(__name__)

DESCRIPTION = """
AgentRail platform API.

Phase 0 exposes the deterministic job slice used to prove the end-to-end request
path: the API records a job in PostgreSQL, publishes its identifier to Redis, and
a worker executes it against the CloudOps sandbox.
""".strip()


def _correlation_id_of(request: Request) -> str:
    context = request.scope.get("correlation_context")
    if isinstance(context, CorrelationContext):
        return context.correlation_id
    return new_correlation_id()


def _problem_response(status_code: int, problem: ProblemDetail) -> JSONResponse:
    # The correlation header is added by CorrelationMiddleware for every
    # response; setting it here too would emit it twice.
    return JSONResponse(status_code=status_code, content=problem.model_dump(mode="json"))


def attach_infrastructure(
    app: FastAPI,
    *,
    engine: AsyncEngine | None = None,
    redis_client: Redis | None = None,
) -> None:
    """Bind the engine, session factory and Redis client to ``app.state``.

    Extracted from the lifespan handler so tests can attach an already-migrated
    engine instead of letting the app build its own.
    """
    settings: ApiSettings = app.state.settings
    resolved_engine = engine if engine is not None else create_database_engine(settings)
    app.state.engine = resolved_engine
    app.state.session_factory = create_session_factory(resolved_engine)
    app.state.redis = redis_client if redis_client is not None else create_redis_client(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: ApiSettings = app.state.settings
    configure_logging(
        service=settings.service_name,
        environment=settings.environment.value,
        level=settings.log_level,
    )

    attach_infrastructure(app)
    logger.info("api_started", extra={"environment": settings.environment.value})

    try:
        yield
    finally:
        # Ordered shutdown: stop accepting queue work, then close the pool.
        await app.state.redis.aclose()
        await app.state.engine.dispose()
        logger.info("api_stopped")


def _use_route_names_as_operation_ids(app: FastAPI) -> None:
    """Give the generated TypeScript client readable method names."""
    for route in app.routes:
        if isinstance(route, APIRoute):
            route.operation_id = route.name


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    resolved = settings or api_settings()

    app = FastAPI(
        title="AgentRail API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=list(resolved.cors_allow_origins),
                # Required for the browser to send the HttpOnly session cookie
                # cross-origin. Safe only because the origin list is explicit —
                # credentialed CORS with a wildcard origin is forbidden, and the
                # settings type makes a wildcard unrepresentable.
                allow_credentials=True,
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=[
                    "content-type",
                    "idempotency-key",
                    "x-correlation-id",
                    "traceparent",
                ],
                expose_headers=["x-correlation-id", "traceparent"],
                max_age=600,
            ),
            Middleware(CorrelationMiddleware),
            Middleware(MaxBodySizeMiddleware, max_bytes=resolved.max_request_bytes),
        ],
    )
    app.state.settings = resolved

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(organisations.router)
    app.include_router(jobs.router)
    _use_route_names_as_operation_ids(app)

    @app.exception_handler(AuthorisationError)
    async def handle_authorisation_error(request: Request, exc: AuthorisationError) -> JSONResponse:
        """Every denial looks identical from the outside.

        The permission that was missing is logged, never returned — telling a
        caller *why* they were refused lets them map another tenant's resources.
        """
        logger.warning(
            "authorisation_denied",
            extra={"permission": exc.permission.value, "organisation_id": exc.organisation_id},
        )
        forbidden = ForbiddenError()
        return _problem_response(
            forbidden.status_code, forbidden.to_problem(_correlation_id_of(request))
        )

    @app.exception_handler(PlatformError)
    async def handle_platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        correlation_id = _correlation_id_of(request)
        logger.warning(
            "platform_error",
            extra={"error_code": exc.code.value, "http_status": exc.status_code},
        )
        return _problem_response(exc.status_code, exc.to_problem(correlation_id))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        problem = ProblemDetail(
            code=ErrorCode.VALIDATION_FAILED,
            message="The request failed validation.",
            correlation_id=_correlation_id_of(request),
            details={
                "errors": [
                    {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
                    for error in exc.errors()
                ]
            },
        )
        return _problem_response(422, problem)

    @app.exception_handler(BodyTooLarge)
    async def handle_body_too_large(request: Request, exc: BodyTooLarge) -> JSONResponse:
        problem = ProblemDetail(
            code=ErrorCode.PAYLOAD_TOO_LARGE,
            message="The request body is too large.",
            correlation_id=_correlation_id_of(request),
            details={"max_bytes": exc.max_bytes},
        )
        return _problem_response(413, problem)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        correlation_id = _correlation_id_of(request)
        # The traceback goes to the log, never to the client.
        logger.exception("unhandled_error", extra={"error_type": type(exc).__name__})
        problem = ProblemDetail(
            code=ErrorCode.INTERNAL_ERROR,
            message="An unexpected error occurred. Quote the correlation id when reporting it.",
            correlation_id=correlation_id,
        )
        return _problem_response(500, problem)

    return app
