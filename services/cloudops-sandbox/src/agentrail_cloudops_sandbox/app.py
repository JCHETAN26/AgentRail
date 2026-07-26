"""HTTP surface for the deterministic sandbox."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware import Middleware

from agentrail_cloudops_sandbox import __version__
from agentrail_cloudops_sandbox.cloudops import (
    FaultMode,
    ScenarioManifest,
    ServiceHealth,
    SideEffectResult,
    ToolContract,
    create_incident,
    escalate_to_human,
    get_dependency_graph,
    get_runbook,
    get_service_health,
    notify_oncall,
    query_metrics,
    reset_scenario,
    restart_service,
    scale_service,
    search_logs,
    seed_state,
)
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


class ToolContractsResponse(BaseModel):
    tools: list[ToolContract]


class ScenarioListResponse(BaseModel):
    scenarios: list[ScenarioManifest]


class SeedResponse(BaseModel):
    active_scenario_id: str
    scenario_count: int


class MetricQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(min_length=1, max_length=100)
    metric: str = Field(min_length=1, max_length=100)
    start_time: str = Field(min_length=1, max_length=50)
    end_time: str = Field(min_length=1, max_length=50)
    fault: FaultMode | None = None


class LogSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(ge=1, le=100)
    fault: FaultMode | None = None


class LogSearchResponse(BaseModel):
    items: list[dict[str, object]]


class RestartServiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=120)
    fault: FaultMode | None = None


class ScaleServiceRequest(RestartServiceRequest):
    replicas: int = Field(ge=1, le=20)


class CreateIncidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    severity: str = Field(pattern=r"^sev[1-4]$")
    evidence: list[str] = Field(min_length=1, max_length=20)
    idempotency_key: str = Field(min_length=8, max_length=120)
    fault: FaultMode | None = None


class NotifyOncallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=120)
    fault: FaultMode | None = None


class EscalateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)
    evidence: list[str] = Field(min_length=1, max_length=20)
    fault: FaultMode | None = None


def _maybe_fault(fault: FaultMode | None, response: Response) -> JSONResponse | None:
    if fault is None:
        return None
    if fault == FaultMode.HTTP_500:
        problem = ProblemDetail(
            code=ErrorCode.INTERNAL_ERROR,
            message="Synthetic fault injection: HTTP 500.",
            correlation_id="cid_sandbox_fault",
        )
        return JSONResponse(status_code=500, content=problem.model_dump(mode="json"))
    if fault == FaultMode.RATE_LIMIT:
        return JSONResponse(
            status_code=429,
            content={"code": "synthetic_rate_limit"},
            headers={"retry-after": "30"},
        )
    if fault == FaultMode.UNAVAILABLE:
        return JSONResponse(status_code=503, content={"code": "synthetic_unavailable"})
    if fault == FaultMode.TIMEOUT:
        return JSONResponse(status_code=504, content={"code": "synthetic_timeout"})
    if fault == FaultMode.MALFORMED:
        return JSONResponse(status_code=200, content={"malformed": True})
    if fault == FaultMode.LATENCY:
        response.headers["x-synthetic-latency-ms"] = "750"
    return None


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
    app.state.sandbox_state = seed_state()

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

    @app.get("/v1/tool-contracts", response_model=ToolContractsResponse, tags=["cloudops"])
    async def list_tool_contracts() -> ToolContractsResponse:
        from agentrail_cloudops_sandbox.cloudops import TOOL_CONTRACTS

        return ToolContractsResponse(tools=list(TOOL_CONTRACTS))

    @app.get("/v1/scenarios", response_model=ScenarioListResponse, tags=["cloudops"])
    async def list_scenarios() -> ScenarioListResponse:
        from agentrail_cloudops_sandbox.cloudops import SCENARIOS

        return ScenarioListResponse(scenarios=list(SCENARIOS))

    @app.post("/v1/seed", response_model=SeedResponse, tags=["cloudops"])
    async def seed() -> SeedResponse:
        from agentrail_cloudops_sandbox.cloudops import SCENARIOS

        app.state.sandbox_state = seed_state()
        return SeedResponse(
            active_scenario_id=app.state.sandbox_state.active_scenario_id,
            scenario_count=len(SCENARIOS),
        )

    @app.post(
        "/v1/scenarios/{scenario_id}/reset", response_model=ScenarioManifest, tags=["cloudops"]
    )
    async def reset(scenario_id: str) -> ScenarioManifest:
        try:
            return reset_scenario(app.state.sandbox_state, scenario_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="scenario_not_found") from exc

    @app.get("/v1/services/{service_name}/health", response_model=ServiceHealth, tags=["cloudops"])
    async def service_health(service_name: str) -> ServiceHealth:
        try:
            return get_service_health(app.state.sandbox_state, service_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="service_not_found") from exc

    @app.post("/v1/metrics/query", tags=["cloudops"])
    async def metrics(request: MetricQueryRequest, response: Response) -> object:
        fault = _maybe_fault(request.fault, response)
        if fault is not None:
            return fault
        try:
            return query_metrics(
                app.state.sandbox_state,
                request.service_name,
                request.metric,
                request.start_time,
                request.end_time,
                stale=request.fault == FaultMode.STALE,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="service_not_found") from exc

    @app.post("/v1/logs/search", response_model=LogSearchResponse, tags=["cloudops"])
    async def logs(request: LogSearchRequest, response: Response) -> object:
        fault = _maybe_fault(request.fault, response)
        if fault is not None:
            return fault
        try:
            entries = search_logs(
                app.state.sandbox_state, request.service_name, request.query, request.limit
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="service_not_found") from exc
        return LogSearchResponse(items=[entry.model_dump(mode="json") for entry in entries])

    @app.get("/v1/services/{service_name}/dependency-graph", tags=["cloudops"])
    async def dependency_graph(service_name: str) -> object:
        try:
            return get_dependency_graph(service_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="service_not_found") from exc

    @app.get("/v1/runbooks/{service_name}/{symptom}", tags=["cloudops"])
    async def runbook(service_name: str, symptom: str) -> object:
        return get_runbook(service_name, symptom)

    @app.post("/v1/services/restart", response_model=SideEffectResult, tags=["cloudops"])
    async def restart(request: RestartServiceRequest, response: Response) -> object:
        fault = _maybe_fault(request.fault, response)
        if fault is not None:
            return fault
        return restart_service(
            app.state.sandbox_state, request.service_name, request.idempotency_key
        )

    @app.post("/v1/services/scale", response_model=SideEffectResult, tags=["cloudops"])
    async def scale(request: ScaleServiceRequest, response: Response) -> object:
        fault = _maybe_fault(request.fault, response)
        if fault is not None:
            return fault
        return scale_service(
            app.state.sandbox_state,
            request.service_name,
            request.replicas,
            request.idempotency_key,
        )

    @app.post("/v1/incidents", response_model=SideEffectResult, tags=["cloudops"])
    async def incident(request: CreateIncidentRequest, response: Response) -> object:
        fault = _maybe_fault(request.fault, response)
        if fault is not None:
            return fault
        return create_incident(
            app.state.sandbox_state,
            request.title,
            request.severity,
            request.evidence,
            request.idempotency_key,
        )

    @app.post(
        "/v1/incidents/{incident_id}/notify", response_model=SideEffectResult, tags=["cloudops"]
    )
    async def notify(incident_id: str, request: NotifyOncallRequest, response: Response) -> object:
        fault = _maybe_fault(request.fault, response)
        if fault is not None:
            return fault
        return notify_oncall(
            app.state.sandbox_state,
            incident_id,
            request.message,
            request.idempotency_key,
        )

    @app.post("/v1/escalations", tags=["cloudops"])
    async def escalation(request: EscalateRequest, response: Response) -> object:
        fault = _maybe_fault(request.fault, response)
        if fault is not None:
            return fault
        return escalate_to_human(request.reason, request.evidence)

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
