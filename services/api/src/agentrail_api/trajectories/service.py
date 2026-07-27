"""Trajectory trace explorer queries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.auth.service import Actor, principal_for_organisation
from agentrail_core.errors import ForbiddenError
from agentrail_core.execution import EvaluationRun, RunItem, RunItemState
from agentrail_core.identity import Permission, Principal, Project, authorize
from agentrail_core.trajectories import (
    Trajectory,
    TrajectoryCheckpoint,
    TrajectoryStep,
    TrajectoryStepType,
)


async def principal_for_trajectory(
    session: AsyncSession, actor: Actor, trajectory_id: str
) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(Trajectory, Trajectory.project_id == Project.id)
        .where(Trajectory.id == trajectory_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def principal_for_run(session: AsyncSession, actor: Actor, run_id: str) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(EvaluationRun, EvaluationRun.project_id == Project.id)
        .where(EvaluationRun.id == run_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def get_trajectory(
    session: AsyncSession, principal: Principal, *, trajectory_id: str
) -> Trajectory:
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    trajectory = await session.scalar(
        select(Trajectory)
        .join(Project, Project.id == Trajectory.project_id)
        .where(Trajectory.id == trajectory_id, Project.organisation_id == principal.organisation_id)
    )
    if trajectory is None:
        raise ForbiddenError()
    return trajectory


async def list_steps(
    session: AsyncSession,
    principal: Principal,
    *,
    trajectory_id: str,
    step_type: TrajectoryStepType | None = None,
) -> list[TrajectoryStep]:
    await get_trajectory(session, principal, trajectory_id=trajectory_id)
    clauses = [TrajectoryStep.trajectory_id == trajectory_id]
    if step_type is not None:
        clauses.append(TrajectoryStep.step_type == step_type)
    rows = await session.scalars(
        select(TrajectoryStep).where(*clauses).order_by(TrajectoryStep.step_index)
    )
    return list(rows.all())


async def list_checkpoints(
    session: AsyncSession, principal: Principal, *, trajectory_id: str
) -> list[TrajectoryCheckpoint]:
    await get_trajectory(session, principal, trajectory_id=trajectory_id)
    rows = await session.scalars(
        select(TrajectoryCheckpoint)
        .where(TrajectoryCheckpoint.trajectory_id == trajectory_id)
        .order_by(TrajectoryCheckpoint.checkpoint_index)
    )
    return list(rows.all())


async def list_run_items(
    session: AsyncSession,
    principal: Principal,
    *,
    run_id: str,
    state: RunItemState | None = None,
) -> list[tuple[RunItem, str | None, str | None]]:
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    clauses = [
        RunItem.run_id == run_id,
        EvaluationRun.id == RunItem.run_id,
        Project.id == EvaluationRun.project_id,
        Project.organisation_id == principal.organisation_id,
    ]
    if state is not None:
        clauses.append(RunItem.state == state)
    rows = await session.execute(
        select(RunItem, Trajectory.id, Trajectory.summary["failing_step_id"].as_string())
        .join(EvaluationRun, EvaluationRun.id == RunItem.run_id)
        .join(Project, Project.id == EvaluationRun.project_id)
        .outerjoin(Trajectory, Trajectory.run_item_id == RunItem.id)
        .where(*clauses)
        .order_by(RunItem.item_index)
    )
    return [(row[0], row[1], row[2]) for row in rows.all()]
