"""Durable evaluation-run endpoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, Path, Response, status
from fastapi.responses import StreamingResponse

from agentrail_api.dependencies import (
    ActorDep,
    ContextDep,
    RedisDep,
    SessionDep,
    SessionFactoryDep,
    SettingsDep,
)
from agentrail_api.execution import service
from agentrail_api.execution.schemas import (
    CreateEvaluationRunRequest,
    EvaluationRunMetricsResponse,
    EvaluationRunProgressResponse,
    EvaluationRunResponse,
    RunRecoveryResponse,
)
from agentrail_core.errors import ProblemDetail
from agentrail_core.execution import TERMINAL_RUN_STATES
from agentrail_core.identity import Permission, authorize
from agentrail_core.logging import get_logger

router = APIRouter(prefix="/api/v1", tags=["evaluation-runs"])
logger = get_logger(__name__)

RunId = Annotated[str, Path(min_length=26, max_length=26)]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
    409: {"model": ProblemDetail, "description": "Idempotency key reused."},
    422: {"model": ProblemDetail, "description": "Validation failed."},
    503: {"model": ProblemDetail, "description": "A required dependency is unavailable."},
}

IdempotencyKeyHeader = Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)]


@router.post(
    "/evaluation-runs",
    response_model=EvaluationRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an evaluation run",
    responses=_ERRORS,
)
async def create_evaluation_run(
    body: CreateEvaluationRunRequest,
    response: Response,
    actor: ActorDep,
    session: SessionDep,
    redis_client: RedisDep,
    settings: SettingsDep,
    context: ContextDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> EvaluationRunResponse:
    principal = await service.principal_for_suite(session, actor, body.evaluation_suite_id)
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)
    run, created, event = await service.create_run(
        session, actor, principal, body, context, idempotency_key=idempotency_key
    )
    await session.commit()
    await session.refresh(run)

    if not created:
        response.status_code = status.HTTP_200_OK
        return EvaluationRunResponse.model_validate(run)

    if event is not None:
        await service.publish_outbox_event(
            session,
            redis_client,
            event_id=event.id,
            run_queue_key=settings.run_queue_key,
        )
    logger.info("evaluation_run_created", extra={"run_id": run.id})
    return EvaluationRunResponse.model_validate(run)


@router.get(
    "/evaluation-runs/{run_id}",
    response_model=EvaluationRunResponse,
    summary="Fetch an evaluation run",
    responses=_ERRORS,
)
async def get_evaluation_run(
    run_id: RunId, actor: ActorDep, session: SessionDep
) -> EvaluationRunResponse:
    principal = await service.principal_for_run(session, actor, run_id)
    run = await service.get_run(session, principal, run_id=run_id)
    return EvaluationRunResponse.model_validate(run)


@router.post(
    "/evaluation-runs/{run_id}/cancel",
    response_model=EvaluationRunResponse,
    summary="Cancel an evaluation run",
    responses=_ERRORS,
)
async def cancel_evaluation_run(
    run_id: RunId, actor: ActorDep, session: SessionDep
) -> EvaluationRunResponse:
    principal = await service.principal_for_run(session, actor, run_id)
    run = await service.cancel_run(session, actor, principal, run_id=run_id)
    await session.commit()
    await session.refresh(run)
    return EvaluationRunResponse.model_validate(run)


@router.get(
    "/evaluation-runs/{run_id}/recovery",
    response_model=RunRecoveryResponse,
    summary="Inspect run reliability: attempts, leases, faults and side effects",
    responses=_ERRORS,
)
async def get_evaluation_run_recovery(
    run_id: RunId, actor: ActorDep, session: SessionDep
) -> RunRecoveryResponse:
    principal = await service.principal_for_run(session, actor, run_id)
    return await service.run_recovery(session, principal, run_id=run_id)


@router.get(
    "/evaluation-runs/{run_id}/metrics",
    response_model=EvaluationRunMetricsResponse,
    summary="Inspect run observability metrics and SLO status",
    responses=_ERRORS,
)
async def get_evaluation_run_metrics(
    run_id: RunId, actor: ActorDep, session: SessionDep
) -> EvaluationRunMetricsResponse:
    principal = await service.principal_for_run(session, actor, run_id)
    return await service.run_metrics(session, principal, run_id=run_id)


@router.get(
    "/evaluation-runs/{run_id}/events",
    response_class=StreamingResponse,
    summary="Stream evaluation-run progress",
    responses=_ERRORS,
)
async def stream_evaluation_run_events(
    run_id: RunId, actor: ActorDep, session: SessionDep, session_factory: SessionFactoryDep
) -> StreamingResponse:
    principal = await service.principal_for_run(session, actor, run_id)
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)

    async def events() -> AsyncIterator[str]:
        while True:
            async with session_factory() as snapshot_session:
                run, item_states = await service.progress_snapshot(
                    snapshot_session, principal, run_id=run_id
                )
            snapshot = EvaluationRunProgressResponse(
                run=EvaluationRunResponse.model_validate(run), item_states=item_states
            )
            payload = json.dumps(snapshot.model_dump(mode="json"), sort_keys=True)
            yield f"event: progress\ndata: {payload}\n\n"
            if run.state in TERMINAL_RUN_STATES:
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream")
