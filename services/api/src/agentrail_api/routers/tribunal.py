"""Multi-agent Safety Tribunal endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from agentrail_api.dependencies import ActorDep, SessionDep
from agentrail_api.execution import service as execution_service
from agentrail_api.tribunal import service
from agentrail_api.tribunal.schemas import TribunalSessionResponse
from agentrail_core.errors import NotFoundError, ProblemDetail

router = APIRouter(prefix="/api/v1", tags=["tribunal"])
RunId = Annotated[str, Path(min_length=26, max_length=26)]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
    422: {"model": ProblemDetail, "description": "Validation failed."},
}


@router.post(
    "/evaluation-runs/{run_id}/tribunal",
    response_model=TribunalSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run the deterministic multi-agent Safety Tribunal",
    responses=_ERRORS,
)
async def create_tribunal_session(
    run_id: RunId,
    response: Response,
    actor: ActorDep,
    session: SessionDep,
) -> TribunalSessionResponse:
    principal = await execution_service.principal_for_run(session, actor, run_id)
    run = await execution_service.get_run(session, principal, run_id=run_id)
    bundle, created = await service.create_tribunal_session(session, actor, principal, run=run)
    await session.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    return service.as_response(bundle)


@router.get(
    "/evaluation-runs/{run_id}/tribunal",
    response_model=TribunalSessionResponse,
    summary="Fetch the deterministic multi-agent Safety Tribunal result",
    responses={**_ERRORS, 404: {"model": ProblemDetail, "description": "No Tribunal session."}},
)
async def get_tribunal_session(
    run_id: RunId,
    actor: ActorDep,
    session: SessionDep,
) -> TribunalSessionResponse:
    principal = await execution_service.principal_for_run(session, actor, run_id)
    run = await execution_service.get_run(session, principal, run_id=run_id)
    bundle = await service.get_tribunal_session(session, principal, run=run)
    if bundle is None:
        raise NotFoundError("No Tribunal session has been created for this run.")
    return service.as_response(bundle)
