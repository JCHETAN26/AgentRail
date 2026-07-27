"""Trajectory trace explorer endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from agentrail_api.dependencies import ActorDep, SessionDep
from agentrail_api.trajectories import service
from agentrail_api.trajectories.schemas import (
    RunItemTraceListResponse,
    RunItemTraceResponse,
    TrajectoryCheckpointListResponse,
    TrajectoryCheckpointResponse,
    TrajectoryResponse,
    TrajectoryStepListResponse,
    TrajectoryStepResponse,
)
from agentrail_core.errors import ProblemDetail
from agentrail_core.execution import RunItemState
from agentrail_core.trajectories import TrajectoryStepType

router = APIRouter(prefix="/api/v1", tags=["trajectories"])

RunId = Annotated[str, Path(min_length=26, max_length=26)]
TrajectoryId = Annotated[str, Path(min_length=26, max_length=26)]
RunItemStateFilter = Annotated[RunItemState | None, Query()]
TrajectoryStepTypeFilter = Annotated[TrajectoryStepType | None, Query()]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
    422: {"model": ProblemDetail, "description": "Validation failed."},
}


@router.get(
    "/evaluation-runs/{run_id}/items",
    response_model=RunItemTraceListResponse,
    summary="List run items with trajectory links",
    responses=_ERRORS,
)
async def list_evaluation_run_items(
    run_id: RunId,
    actor: ActorDep,
    session: SessionDep,
    state: RunItemStateFilter = None,
) -> RunItemTraceListResponse:
    principal = await service.principal_for_run(session, actor, run_id)
    rows = await service.list_run_items(session, principal, run_id=run_id, state=state)
    return RunItemTraceListResponse(
        items=[
            RunItemTraceResponse(
                id=item.id,
                run_id=item.run_id,
                item_index=item.item_index,
                partition=item.partition,
                state=item.state,
                trajectory_id=trajectory_id,
                failing_step_id=failing_step_id,
                error_code=item.error_code,
                error_message=item.error_message,
            )
            for item, trajectory_id, failing_step_id in rows
        ]
    )


@router.get(
    "/trajectories/{trajectory_id}",
    response_model=TrajectoryResponse,
    summary="Fetch a trajectory",
    responses=_ERRORS,
)
async def get_trajectory(
    trajectory_id: TrajectoryId, actor: ActorDep, session: SessionDep
) -> TrajectoryResponse:
    principal = await service.principal_for_trajectory(session, actor, trajectory_id)
    trajectory = await service.get_trajectory(session, principal, trajectory_id=trajectory_id)
    return TrajectoryResponse.model_validate(trajectory)


@router.get(
    "/trajectories/{trajectory_id}/steps",
    response_model=TrajectoryStepListResponse,
    summary="List trajectory steps",
    responses=_ERRORS,
)
async def list_trajectory_steps(
    trajectory_id: TrajectoryId,
    actor: ActorDep,
    session: SessionDep,
    step_type: TrajectoryStepTypeFilter = None,
) -> TrajectoryStepListResponse:
    principal = await service.principal_for_trajectory(session, actor, trajectory_id)
    steps = await service.list_steps(
        session, principal, trajectory_id=trajectory_id, step_type=step_type
    )
    return TrajectoryStepListResponse(
        items=[TrajectoryStepResponse.model_validate(step) for step in steps]
    )


@router.get(
    "/trajectories/{trajectory_id}/checkpoints",
    response_model=TrajectoryCheckpointListResponse,
    summary="List trajectory checkpoints",
    responses=_ERRORS,
)
async def list_trajectory_checkpoints(
    trajectory_id: TrajectoryId, actor: ActorDep, session: SessionDep
) -> TrajectoryCheckpointListResponse:
    principal = await service.principal_for_trajectory(session, actor, trajectory_id)
    checkpoints = await service.list_checkpoints(session, principal, trajectory_id=trajectory_id)
    return TrajectoryCheckpointListResponse(
        items=[
            TrajectoryCheckpointResponse.model_validate(checkpoint) for checkpoint in checkpoints
        ]
    )
