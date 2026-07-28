"""Multi-agent Safety Tribunal endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from agentrail_api.dependencies import ActorDep, SessionDep, SettingsDep
from agentrail_api.execution import service as execution_service
from agentrail_api.tribunal import service
from agentrail_api.tribunal.schemas import (
    CreateTribunalReplayRequest,
    TribunalReplayListResponse,
    TribunalReplayResponse,
    TribunalSessionResponse,
)
from agentrail_core.errors import NotFoundError, ProblemDetail

router = APIRouter(prefix="/api/v1", tags=["tribunal"])
RunId = Annotated[str, Path(min_length=26, max_length=26)]
TribunalSessionId = Annotated[str, Path(min_length=26, max_length=26)]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
    422: {"model": ProblemDetail, "description": "Validation failed."},
}


@router.post(
    "/evaluation-runs/{run_id}/tribunal",
    response_model=TribunalSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run the multi-agent Safety Tribunal",
    responses=_ERRORS,
)
async def create_tribunal_session(
    run_id: RunId,
    response: Response,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> TribunalSessionResponse:
    principal = await execution_service.principal_for_run(session, actor, run_id)
    run = await execution_service.get_run(session, principal, run_id=run_id)
    bundle, created = await service.create_tribunal_session(
        session,
        actor,
        principal,
        run=run,
        openai_api_key=settings.openai_api_key,
        openai_base_url=settings.openai_base_url,
        model_timeout_seconds=settings.tribunal_model_timeout_seconds,
    )
    await session.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    return service.as_response(bundle)


@router.get(
    "/evaluation-runs/{run_id}/tribunal",
    response_model=TribunalSessionResponse,
    summary="Fetch the multi-agent Safety Tribunal result",
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


@router.post(
    "/tribunal-sessions/{tribunal_session_id}/replays",
    response_model=TribunalReplayResponse,
    summary="Create a forkable Tribunal replay",
    responses=_ERRORS,
)
async def create_tribunal_replay(
    tribunal_session_id: TribunalSessionId,
    body: CreateTribunalReplayRequest,
    actor: ActorDep,
    session: SessionDep,
) -> TribunalReplayResponse:
    principal = await service.principal_for_tribunal_session(session, actor, tribunal_session_id)
    replay = await service.create_replay(
        session, actor, principal, tribunal_session_id=tribunal_session_id, request=body
    )
    await session.commit()
    await session.refresh(replay)
    return service.replay_as_response(replay)


@router.get(
    "/tribunal-sessions/{tribunal_session_id}/replays",
    response_model=TribunalReplayListResponse,
    summary="List Tribunal replays",
    responses=_ERRORS,
)
async def list_tribunal_replays(
    tribunal_session_id: TribunalSessionId,
    actor: ActorDep,
    session: SessionDep,
) -> TribunalReplayListResponse:
    principal = await service.principal_for_tribunal_session(session, actor, tribunal_session_id)
    replays = await service.list_replays(
        session, principal, tribunal_session_id=tribunal_session_id
    )
    return TribunalReplayListResponse(
        items=[service.replay_as_response(replay) for replay in replays]
    )
