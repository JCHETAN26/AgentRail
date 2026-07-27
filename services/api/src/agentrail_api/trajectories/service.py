"""Trajectory trace explorer queries."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.auth.service import Actor, principal_for_organisation
from agentrail_api.identity.service import record_audit
from agentrail_api.trajectories.schemas import CreateTrajectoryReplayRequest
from agentrail_core.errors import ForbiddenError
from agentrail_core.execution import EvaluationRun, RunItem, RunItemState
from agentrail_core.identity import Permission, Principal, Project, authorize
from agentrail_core.ids import new_sortable_id
from agentrail_core.trajectories import (
    ReplayMode,
    ReplayState,
    Trajectory,
    TrajectoryCheckpoint,
    TrajectoryReplay,
    TrajectoryStep,
    TrajectoryStepType,
    redact_payload,
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


async def list_replays(
    session: AsyncSession, principal: Principal, *, trajectory_id: str
) -> list[TrajectoryReplay]:
    await get_trajectory(session, principal, trajectory_id=trajectory_id)
    rows = await session.scalars(
        select(TrajectoryReplay)
        .where(TrajectoryReplay.trajectory_id == trajectory_id)
        .order_by(TrajectoryReplay.created_at, TrajectoryReplay.id)
    )
    return list(rows.all())


async def create_replay(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    trajectory_id: str,
    request: CreateTrajectoryReplayRequest,
) -> TrajectoryReplay:
    trajectory = await get_trajectory(session, principal, trajectory_id=trajectory_id)
    # Creating a replay persists a row and an audit event, so it is a write even
    # though every byte it reads is already readable. ``get_trajectory`` above
    # only proves ``run:read``; a viewer must not get past here.
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)
    checkpoint = await _checkpoint_for_replay(
        session, trajectory_id=trajectory_id, checkpoint_id=request.checkpoint_id
    )
    steps = await list_steps(session, principal, trajectory_id=trajectory_id)
    source_digest = _trajectory_digest(trajectory=trajectory, steps=steps, checkpoint=checkpoint)
    redacted_request, request_redaction = redact_payload(request.model_dump(mode="json"))
    # The digest is taken over the *raw* overrides. Redacting first would make
    # two forks that differ only in a sensitive-keyed value — ``{"token": "a"}``
    # and ``{"token": "b"}`` — collapse onto one digest and report no
    # divergence. A SHA-256 is not a replayable form of the value, and only the
    # redacted copy is ever persisted or returned.
    raw_overrides = request.fork_overrides or {}
    fork_overrides, override_redaction = redact_payload(raw_overrides)
    replay_digest = (
        source_digest
        if request.mode == ReplayMode.RECORDED
        else _digest(
            {
                "source_digest": source_digest,
                "mode": request.mode,
                "checkpoint": checkpoint.state if checkpoint is not None else None,
                "fork_overrides": raw_overrides,
            }
        )
    )
    divergence = _divergence_summary(
        mode=request.mode,
        source_digest=source_digest,
        replay_digest=replay_digest,
        checkpoint=checkpoint,
        fork_overrides=fork_overrides,
    )
    replay = TrajectoryReplay(
        id=new_sortable_id(),
        project_id=trajectory.project_id,
        trajectory_id=trajectory.id,
        source_checkpoint_id=checkpoint.id if checkpoint is not None else None,
        mode=request.mode,
        state=ReplayState.COMPLETED,
        source_digest=source_digest,
        replay_digest=replay_digest,
        request={
            **redacted_request,
            "redaction_summary": {
                "request": request_redaction,
                "fork_overrides": override_redaction,
            },
        },
        result={
            "reproduced": replay_digest == source_digest,
            "step_count": len(steps),
            "checkpoint_label": checkpoint.label if checkpoint is not None else None,
        },
        divergence=divergence,
        safety_summary={
            "side_effect_policy": "never_replay_original",
            "original_side_effect_replayed": False,
            "replayed_side_effect_count": 0,
            # What happened, not what was asked for. ``create_replay`` invokes no
            # model in any mode, so a "live" request produces a live-*labelled*
            # record and this stays false until a real executor exists. The mode
            # the caller asked for is on the row already.
            "live_model_calls": 0,
            "executed_live": False,
        },
        created_by=actor.user.id if actor.user else None,
        completed_at=datetime.now(UTC),
    )
    session.add(replay)
    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="trajectory_replay.created",
        target_type="trajectory_replay",
        target_id=replay.id,
        context={
            "trajectory_id": trajectory.id,
            "mode": request.mode,
            "source_checkpoint_id": replay.source_checkpoint_id,
            "source_digest": source_digest,
            "replay_digest": replay_digest,
        },
    )
    await session.flush()
    return replay


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


async def _checkpoint_for_replay(
    session: AsyncSession, *, trajectory_id: str, checkpoint_id: str | None
) -> TrajectoryCheckpoint | None:
    if checkpoint_id is None:
        return None
    checkpoint = await session.scalar(
        select(TrajectoryCheckpoint).where(
            TrajectoryCheckpoint.id == checkpoint_id,
            TrajectoryCheckpoint.trajectory_id == trajectory_id,
        )
    )
    if checkpoint is None:
        raise ForbiddenError()
    return checkpoint


def _trajectory_digest(
    *,
    trajectory: Trajectory,
    steps: list[TrajectoryStep],
    checkpoint: TrajectoryCheckpoint | None,
) -> str:
    return _digest(
        {
            "trajectory_id": trajectory.id,
            "state": trajectory.state,
            "summary": trajectory.summary,
            "graph_state": trajectory.graph_state,
            "final_checkpoint": trajectory.final_checkpoint,
            "checkpoint": checkpoint.state if checkpoint is not None else None,
            "steps": [
                {
                    "step_index": step.step_index,
                    "step_type": step.step_type,
                    "title": step.title,
                    "input": step.redacted_input,
                    "output": step.redacted_output,
                    "evidence": step.evidence,
                    "checkpoint": step.checkpoint,
                }
                for step in steps
            ],
        }
    )


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _divergence_summary(
    *,
    mode: ReplayMode,
    source_digest: str,
    replay_digest: str,
    checkpoint: TrajectoryCheckpoint | None,
    fork_overrides: Any,
) -> dict[str, Any]:
    diverged = replay_digest != source_digest
    return {
        "diverged": diverged,
        "mode": mode,
        "source_digest": source_digest,
        "replay_digest": replay_digest,
        "checkpoint_id": checkpoint.id if checkpoint is not None else None,
        "checkpoint_label": checkpoint.label if checkpoint is not None else None,
        "changed_fields": sorted(fork_overrides.keys()) if isinstance(fork_overrides, dict) else [],
    }
