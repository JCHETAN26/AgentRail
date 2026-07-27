"""Claim and execute durable evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.execution import (
    EvaluationRun,
    EvaluationRunState,
    OutboxEvent,
    RunItem,
    RunItemState,
)
from agentrail_core.ids import new_sortable_id
from agentrail_core.logging import get_logger
from agentrail_core.trajectories import (
    Trajectory,
    TrajectoryCheckpoint,
    TrajectoryState,
    TrajectoryStep,
    TrajectoryStepType,
    redact_payload,
)

logger = get_logger(__name__)


class RunOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    id: str
    item_count: int


class EvaluationRunRunner:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        worker_id: str,
        lease_seconds: float = 30.0,
    ) -> None:
        self._session_factory = session_factory
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds

    async def process(self, run_id: str) -> RunOutcome:
        claimed = await self._claim_run(run_id)
        if claimed is None:
            return await self._classify_unclaimable(run_id)
        logger.info("evaluation_run_claimed", extra={"run_id": claimed.id})

        while True:
            if await self._is_cancelled(claimed.id):
                await self._cancel_open_items(claimed.id)
                return RunOutcome.CANCELLED
            item = await self._lease_next_item(claimed.id)
            if item is None:
                break
            await self._execute_item(item)

        return await self._aggregate(claimed.id)

    async def _claim_run(self, run_id: str) -> ClaimedRun | None:
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(EvaluationRun)
                .where(
                    EvaluationRun.id == run_id,
                    EvaluationRun.state == EvaluationRunState.RUNNING,
                )
                .with_for_update(skip_locked=True)
            )
            if existing is not None:
                await session.commit()
                return ClaimedRun(id=existing.id, item_count=existing.item_count)

            validating = (
                update(EvaluationRun)
                .where(
                    EvaluationRun.id == run_id, EvaluationRun.state == EvaluationRunState.CREATED
                )
                .values(
                    state=EvaluationRunState.VALIDATING,
                    updated_at=func.now(),
                    version=EvaluationRun.version + 1,
                )
            )
            await session.execute(validating)
            queuing = (
                update(EvaluationRun)
                .where(
                    EvaluationRun.id == run_id, EvaluationRun.state == EvaluationRunState.VALIDATING
                )
                .values(
                    state=EvaluationRunState.QUEUING,
                    updated_at=func.now(),
                    version=EvaluationRun.version + 1,
                )
            )
            await session.execute(queuing)
            running = (
                update(EvaluationRun)
                .where(
                    EvaluationRun.id == run_id, EvaluationRun.state == EvaluationRunState.QUEUING
                )
                .values(
                    state=EvaluationRunState.RUNNING,
                    started_at=func.now(),
                    updated_at=func.now(),
                    version=EvaluationRun.version + 1,
                )
                .returning(EvaluationRun.id, EvaluationRun.item_count)
            )
            row = (await session.execute(running)).one_or_none()
            await session.commit()
        if row is None:
            return None
        return ClaimedRun(id=row.id, item_count=row.item_count)

    async def _classify_unclaimable(self, run_id: str) -> RunOutcome:
        async with self._session_factory() as session:
            state = await session.scalar(
                select(EvaluationRun.state).where(EvaluationRun.id == run_id)
            )
        if state is None:
            logger.warning("evaluation_run_missing", extra={"run_id": run_id})
            return RunOutcome.MISSING
        if state == EvaluationRunState.CANCELLED:
            return RunOutcome.CANCELLED
        logger.info("evaluation_run_already_handled", extra={"run_id": run_id, "state": state})
        return RunOutcome.SKIPPED

    async def _is_cancelled(self, run_id: str) -> bool:
        async with self._session_factory() as session:
            state = await session.scalar(
                select(EvaluationRun.state).where(EvaluationRun.id == run_id)
            )
        return state == EvaluationRunState.CANCELLED

    async def _lease_next_item(self, run_id: str) -> RunItem | None:
        lease_expires_at = datetime.now(UTC) + timedelta(seconds=self._lease_seconds)
        async with self._session_factory() as session:
            item_id = await session.scalar(
                select(RunItem.id)
                .where(RunItem.run_id == run_id, RunItem.state == RunItemState.PENDING)
                .order_by(RunItem.item_index)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if item_id is None:
                await session.rollback()
                return None
            row = (
                await session.execute(
                    update(RunItem)
                    .where(RunItem.id == item_id, RunItem.state == RunItemState.PENDING)
                    .values(
                        state=RunItemState.LEASED,
                        attempt_count=RunItem.attempt_count + 1,
                        worker_id=self._worker_id,
                        lease_expires_at=lease_expires_at,
                        updated_at=func.now(),
                        version=RunItem.version + 1,
                    )
                    .returning(RunItem)
                )
            ).scalar_one_or_none()
            await session.commit()
        return row

    async def _execute_item(self, item: RunItem) -> None:
        async with self._session_factory() as session:
            run = await session.get(EvaluationRun, item.run_id)
            if run is None:
                return
            claim = await session.execute(
                update(RunItem)
                .where(RunItem.id == item.id, RunItem.state == RunItemState.LEASED)
                .values(
                    state=RunItemState.EXECUTING,
                    started_at=func.now(),
                    checkpoint={"stage": "executing", "item_index": item.item_index},
                    updated_at=func.now(),
                    version=RunItem.version + 1,
                )
            )
            if claim.rowcount != 1:
                await session.rollback()
                return
            trajectory = await self._create_trajectory(session, item=item, run=run)
            graph_checkpoint = await self._append_step(
                session,
                trajectory_id=trajectory.id,
                step_index=1,
                step_type=TrajectoryStepType.GRAPH_STATE,
                title="Load graph state",
                input_payload={"item_index": item.item_index, "partition": item.partition},
                output_payload={
                    "node": "recorded_executor",
                    "state": "ready",
                    "candidate_agent_version_id": run.candidate_agent_version_id,
                },
                checkpoint={"stage": "graph_state", "node": "recorded_executor"},
            )
            await self._append_checkpoint(
                session,
                trajectory_id=trajectory.id,
                step_id=graph_checkpoint.id,
                checkpoint_index=0,
                label="graph-state-ready",
                state=graph_checkpoint.checkpoint,
            )
            await session.execute(
                update(RunItem)
                .where(RunItem.id == item.id, RunItem.state == RunItemState.EXECUTING)
                .values(
                    state=RunItemState.EVALUATING,
                    checkpoint={
                        "stage": "evaluating",
                        "item_index": item.item_index,
                        "trajectory_id": trajectory.id,
                    },
                    updated_at=func.now(),
                    version=RunItem.version + 1,
                )
            )
            await self._append_step(
                session,
                trajectory_id=trajectory.id,
                step_index=2,
                step_type=TrajectoryStepType.TOOL_CALL,
                title="Recorded tool call",
                input_payload={
                    "tool": "recorded_success",
                    "arguments": {
                        "service": f"service-{item.item_index}",
                        "api_key": "test-secret-key",
                    },
                },
                output_payload={"status": "ok", "latency_ms": 0},
                checkpoint={"stage": "tool_call", "tool": "recorded_success"},
                latency_ms=0,
            )
            await self._append_step(
                session,
                trajectory_id=trajectory.id,
                step_index=3,
                step_type=TrajectoryStepType.EVIDENCE,
                title="Collect evidence",
                input_payload={"source": "recorded_fixture"},
                output_payload={"evidence": [{"kind": "deterministic", "supports": "passed"}]},
                evidence={"items": [{"kind": "deterministic", "supports": "passed"}]},
                checkpoint={"stage": "evidence", "evidence_count": 1},
            )
            final_checkpoint = await self._append_step(
                session,
                trajectory_id=trajectory.id,
                step_index=4,
                step_type=TrajectoryStepType.CHECKPOINT,
                title="Persist final checkpoint",
                input_payload={"item_index": item.item_index},
                output_payload={"checkpoint": "completed"},
                checkpoint={"stage": "completed", "item_index": item.item_index, "passed": True},
            )
            await self._append_checkpoint(
                session,
                trajectory_id=trajectory.id,
                step_id=final_checkpoint.id,
                checkpoint_index=1,
                label="item-completed",
                state=final_checkpoint.checkpoint,
            )
            await self._append_step(
                session,
                trajectory_id=trajectory.id,
                step_index=5,
                step_type=TrajectoryStepType.FINAL_RESULT,
                title="Recorded final result",
                input_payload={"threshold": "recorded_success"},
                output_payload={"passed": True, "mode": "recorded"},
                evidence={"result_supported_by_step": 3},
                checkpoint={"stage": "final_result", "passed": True},
            )
            trajectory.state = TrajectoryState.COMPLETED
            trajectory.summary = {
                "step_count": 6,
                "checkpoint_count": 2,
                "result": "passed",
                "failing_step_id": None,
            }
            trajectory.graph_state = {
                "node": "recorded_executor",
                "state": "completed",
                "item_index": item.item_index,
            }
            trajectory.final_checkpoint = final_checkpoint.checkpoint
            trajectory.completed_at = datetime.now(UTC)
            trajectory.updated_at = trajectory.completed_at
            await session.execute(
                update(RunItem)
                .where(RunItem.id == item.id, RunItem.state == RunItemState.EVALUATING)
                .values(
                    state=RunItemState.COMPLETED,
                    result={"passed": True, "mode": "recorded", "trajectory_id": trajectory.id},
                    checkpoint={
                        "stage": "completed",
                        "item_index": item.item_index,
                        "trajectory_id": trajectory.id,
                    },
                    lease_expires_at=None,
                    completed_at=func.now(),
                    updated_at=func.now(),
                    version=RunItem.version + 1,
                )
            )
            await session.commit()

    async def _create_trajectory(
        self, session: AsyncSession, *, item: RunItem, run: EvaluationRun
    ) -> Trajectory:
        existing = await session.scalar(
            select(Trajectory).where(Trajectory.run_item_id == item.id).with_for_update()
        )
        if existing is not None:
            return existing
        trajectory = Trajectory(
            id=new_sortable_id(),
            project_id=run.project_id,
            run_id=run.id,
            run_item_id=item.id,
            item_index=item.item_index,
            state=TrajectoryState.RUNNING,
            summary={"mode": run.execution_mode},
            graph_state={"node": "recorded_executor", "state": "created"},
            final_checkpoint={},
        )
        session.add(trajectory)
        await self._append_step(
            session,
            trajectory_id=trajectory.id,
            step_index=0,
            step_type=TrajectoryStepType.INPUT,
            title="Load dataset item",
            input_payload={
                "dataset_version_id": item.checkpoint.get("dataset_version_id"),
                "item_index": item.item_index,
                "partition": item.partition,
                "requester_email": "operator@example.com",
            },
            output_payload={"loaded": True, "item_index": item.item_index},
            checkpoint={"stage": "input", "item_index": item.item_index},
        )
        return trajectory

    async def _append_step(
        self,
        session: AsyncSession,
        *,
        trajectory_id: str,
        step_index: int,
        step_type: TrajectoryStepType,
        title: str,
        input_payload: dict[str, object],
        output_payload: dict[str, object],
        checkpoint: dict[str, object],
        evidence: dict[str, object] | None = None,
        latency_ms: int | None = None,
    ) -> TrajectoryStep:
        existing = await session.scalar(
            select(TrajectoryStep)
            .where(
                TrajectoryStep.trajectory_id == trajectory_id,
                TrajectoryStep.step_index == step_index,
            )
            .with_for_update()
        )
        if existing is not None:
            return existing
        redacted_input, input_summary = redact_payload(input_payload)
        redacted_output, output_summary = redact_payload(output_payload)
        redacted_checkpoint, checkpoint_summary = redact_payload(checkpoint)
        redacted_evidence, evidence_summary = redact_payload(evidence or {})
        step = TrajectoryStep(
            id=new_sortable_id(),
            trajectory_id=trajectory_id,
            step_index=step_index,
            step_type=step_type,
            title=title,
            redacted_input=redacted_input,
            redacted_output=redacted_output,
            evidence=redacted_evidence,
            checkpoint=redacted_checkpoint,
            redaction_summary={
                "input": input_summary,
                "output": output_summary,
                "checkpoint": checkpoint_summary,
                "evidence": evidence_summary,
            },
            latency_ms=latency_ms,
        )
        session.add(step)
        await session.flush()
        return step

    async def _append_checkpoint(
        self,
        session: AsyncSession,
        *,
        trajectory_id: str,
        step_id: str,
        checkpoint_index: int,
        label: str,
        state: dict[str, object],
    ) -> None:
        existing = await session.scalar(
            select(TrajectoryCheckpoint)
            .where(
                TrajectoryCheckpoint.trajectory_id == trajectory_id,
                TrajectoryCheckpoint.checkpoint_index == checkpoint_index,
            )
            .with_for_update()
        )
        if existing is not None:
            return
        redacted_state, _summary = redact_payload(state)
        session.add(
            TrajectoryCheckpoint(
                id=new_sortable_id(),
                trajectory_id=trajectory_id,
                step_id=step_id,
                checkpoint_index=checkpoint_index,
                label=label,
                state=redacted_state,
            )
        )

    async def _aggregate(self, run_id: str) -> RunOutcome:
        async with self._session_factory() as session:
            counts = {
                RunItemState(state): int(count)
                for state, count in (
                    await session.execute(
                        select(RunItem.state, func.count())
                        .where(RunItem.run_id == run_id)
                        .group_by(RunItem.state)
                    )
                ).all()
            }
            failed = counts.get(RunItemState.FAILED_TERMINAL, 0)
            completed = counts.get(RunItemState.COMPLETED, 0)
            terminal = completed + failed + counts.get(RunItemState.CANCELLED, 0)
            run = await session.scalar(
                select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update()
            )
            if run is None:
                return RunOutcome.MISSING
            if run.state == EvaluationRunState.CANCELLED:
                return RunOutcome.CANCELLED
            if run.state != EvaluationRunState.RUNNING:
                return RunOutcome.SKIPPED
            if terminal < run.item_count:
                return RunOutcome.SKIPPED
            run.state = EvaluationRunState.AGGREGATING
            run.completed_count = completed
            run.failed_count = failed
            run.summary = {
                **run.summary,
                "completed_count": completed,
                "failed_count": failed,
                "item_states": {state.value: count for state, count in counts.items()},
            }
            run.version += 1
            await session.flush()
            run.state = EvaluationRunState.PASSED if failed == 0 else EvaluationRunState.FAILED
            run.completed_at = datetime.now(UTC)
            run.updated_at = run.completed_at
            run.version += 1
            await session.commit()
        return RunOutcome.PASSED if failed == 0 else RunOutcome.FAILED

    async def _cancel_open_items(self, run_id: str) -> None:
        await self.cancel_open_items(self._session_factory, run_id=run_id)

    @staticmethod
    async def cancel_open_items(
        session_factory: async_sessionmaker[AsyncSession], *, run_id: str
    ) -> int:
        async with session_factory() as session:
            updated = (
                await session.execute(
                    update(RunItem)
                    .where(
                        RunItem.run_id == run_id,
                        RunItem.state.not_in(
                            (
                                RunItemState.COMPLETED.value,
                                RunItemState.FAILED_TERMINAL.value,
                                RunItemState.CANCELLED.value,
                            )
                        ),
                    )
                    .values(
                        state=RunItemState.CANCELLED, completed_at=func.now(), updated_at=func.now()
                    )
                )
            ).rowcount
            await session.commit()
        return int(updated or 0)

    @staticmethod
    async def recover_expired_leases(
        session_factory: async_sessionmaker[AsyncSession], *, now: datetime
    ) -> int:
        async with session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(
                            RunItem.id,
                            RunItem.state,
                            RunItem.lease_expires_at,
                            RunItem.attempt_count,
                            RunItem.max_attempts,
                            RunItem.version,
                        )
                        .where(
                            RunItem.state.in_(
                                (
                                    RunItemState.LEASED,
                                    RunItemState.EXECUTING,
                                    RunItemState.EVALUATING,
                                )
                            ),
                            RunItem.lease_expires_at < now,
                        )
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            updated = 0
            for row in rows:
                next_state = (
                    RunItemState.PENDING
                    if row.attempt_count < row.max_attempts
                    else RunItemState.FAILED_TERMINAL
                )
                result = await session.execute(
                    update(RunItem)
                    .where(
                        RunItem.id == row.id,
                        RunItem.state == row.state,
                        RunItem.lease_expires_at == row.lease_expires_at,
                        RunItem.version == row.version,
                    )
                    .values(
                        state=next_state,
                        worker_id=None,
                        lease_expires_at=None,
                        updated_at=func.now(),
                        version=RunItem.version + 1,
                    )
                )
                updated += int(result.rowcount or 0)
            await session.commit()
        return updated

    @staticmethod
    async def recoverable_run_ids(
        session_factory: async_sessionmaker[AsyncSession], *, before: datetime, limit: int
    ) -> list[str]:
        async with session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(EvaluationRun.id)
                        .where(
                            EvaluationRun.state.in_(
                                (
                                    EvaluationRunState.CREATED,
                                    EvaluationRunState.VALIDATING,
                                    EvaluationRunState.QUEUING,
                                    EvaluationRunState.RUNNING,
                                )
                            ),
                            EvaluationRun.updated_at < before,
                        )
                        .order_by(EvaluationRun.updated_at)
                        .limit(limit)
                    )
                ).scalars()
            )
        return rows


async def pending_outbox_run_ids(
    session_factory: async_sessionmaker[AsyncSession], *, limit: int
) -> list[tuple[str, str]]:
    async with session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(OutboxEvent.id, OutboxEvent.aggregate_id)
                    .where(
                        OutboxEvent.published_at.is_(None),
                        OutboxEvent.event_type == "evaluation_run.created",
                    )
                    .order_by(OutboxEvent.created_at)
                    .limit(limit)
                )
            ).all()
        )
    return [(row.id, row.aggregate_id) for row in rows]


async def mark_outbox_published(
    session_factory: async_sessionmaker[AsyncSession], *, event_id: str
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id, OutboxEvent.published_at.is_(None))
            .values(published_at=func.now(), attempts=OutboxEvent.attempts + 1)
        )
        await session.commit()
