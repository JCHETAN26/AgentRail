"""Claim and execute durable evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.datasets import EvaluationSuite
from agentrail_core.evaluators import (
    ComparisonReport,
    EvaluationResult,
    EvaluatorKind,
    EvaluatorVersion,
    aggregate_results,
    canonical_digest,
    default_evaluators,
    score_run_item,
)
from agentrail_core.execution import (
    EvaluationRun,
    EvaluationRunState,
    OutboxEvent,
    RunItem,
    RunItemState,
)
from agentrail_core.faults import (
    FaultProfile,
    FaultProfileError,
    parse_fault_profiles,
    plan_fault,
)
from agentrail_core.ids import new_sortable_id
from agentrail_core.logging import get_logger
from agentrail_core.reliability import BudgetExceededError, BudgetKind, BudgetLedger
from agentrail_core.side_effects import apply_side_effect_once
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


#: Attempts share one trajectory — it is keyed by run item, not by attempt — so
#: fault steps need an index band of their own that the clean-path steps (0-5)
#: can never collide with, however many times an item is retried.
_FAULT_STEP_BASE = 50

#: The one side-effecting call the recorded executor makes. Named after the
#: CloudOps remediation tool it stands in for, so the ledger reads the way it
#: will once a real agent runtime is choosing the tool.
_REMEDIATION_TOOL = "restart_service"


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

            attempt = max(item.attempt_count, 1)
            try:
                profiles = await self._fault_profiles(session, run=run)
            except FaultProfileError as invalid:
                # Suite creation rejects these, but a row written before that
                # validation existed can still reach here. Fail the item, not
                # the worker: an uncaught raise would leave this item leased and
                # take the consumer down with every other run behind it.
                logger.warning(
                    "fault_profile_unexecutable",
                    extra={"run_id": run.id, "item_id": item.id, "reason": invalid.reason},
                )
                await self._fail_item(
                    session,
                    item=item,
                    trajectory=trajectory,
                    attempt=attempt,
                    retryable=False,
                    error_code="fault_profile_invalid",
                    error_message=str(invalid),
                    fault_payload=None,
                    budget_state={},
                    title="Unexecutable fault profile",
                )
                await session.commit()
                return
            fault = plan_fault(profiles, item_index=item.item_index, attempt=attempt)
            # Budgets are per item, so a retry resumes the previous attempt's
            # spend rather than starting over with a fresh allowance.
            ledger = BudgetLedger.restore(
                await self._budget_limits(session, run=run), item.budget_state
            )

            arguments = {
                "service": f"service-{item.item_index}",
                "api_key": "test-secret-key",
            }
            try:
                ledger = ledger.charge(BudgetKind.TOOL_CALLS, 1)
                ledger = ledger.charge(BudgetKind.LOOP_ITERATIONS, 1)
                ledger = ledger.charge(BudgetKind.TOKENS, 1_000)
            except BudgetExceededError as exceeded:
                await self._fail_item(
                    session,
                    item=item,
                    trajectory=trajectory,
                    attempt=attempt,
                    retryable=False,
                    error_code="budget_exhausted",
                    error_message=str(exceeded),
                    fault_payload=None,
                    budget_state=exceeded.ledger.as_payload(),
                    title=f"Budget exhausted: {exceeded.kind.value}",
                )
                await session.commit()
                return

            # The effect reaches the world *before* any injected fault kills the
            # attempt. That ordering is the whole point: a retry then has to
            # find the ledger row and decline to act a second time.
            record, applied = await apply_side_effect_once(
                session,
                project_id=run.project_id,
                run_id=run.id,
                run_item_id=item.id,
                step_index=2,
                tool=_REMEDIATION_TOOL,
                arguments=arguments,
                attempt=attempt,
                result={"status": "ok", "restarted": True},
            )
            await self._append_step(
                session,
                trajectory_id=trajectory.id,
                step_index=2,
                step_type=TrajectoryStepType.TOOL_CALL,
                title="Recorded tool call",
                input_payload={"tool": _REMEDIATION_TOOL, "arguments": arguments},
                output_payload={
                    "status": "ok",
                    "latency_ms": 0,
                    "side_effect_applied": applied,
                    "idempotent_replay": not applied,
                    "applied_on_attempt": record.applied_on_attempt,
                },
                checkpoint={"stage": "tool_call", "tool": _REMEDIATION_TOOL},
                latency_ms=0,
            )

            if fault is not None:
                retryable = fault.retryable and attempt < item.max_attempts
                await self._fail_item(
                    session,
                    item=item,
                    trajectory=trajectory,
                    attempt=attempt,
                    retryable=retryable,
                    error_code=fault.kind.value,
                    error_message=f"Injected {fault.family.value} fault {fault.kind.value}.",
                    fault_payload=fault.as_payload(),
                    budget_state=ledger.as_payload(),
                    title=f"Injected fault: {fault.kind.value}",
                )
                await session.commit()
                return
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
                    result={
                        "passed": True,
                        "mode": "recorded",
                        "trajectory_id": trajectory.id,
                        "side_effect_applied_on_attempt": record.applied_on_attempt,
                    },
                    checkpoint={
                        "stage": "completed",
                        "item_index": item.item_index,
                        "trajectory_id": trajectory.id,
                    },
                    injected_fault=None,
                    budget_state=ledger.as_payload(),
                    lease_expires_at=None,
                    completed_at=func.now(),
                    updated_at=func.now(),
                    version=RunItem.version + 1,
                )
            )
            await session.commit()

    async def _fault_profiles(
        self, session: AsyncSession, *, run: EvaluationRun
    ) -> tuple[FaultProfile, ...]:
        suite = await session.get(EvaluationSuite, run.evaluation_suite_id)
        if suite is None:
            return ()
        return parse_fault_profiles(suite.fault_profiles)

    async def _budget_limits(
        self, session: AsyncSession, *, run: EvaluationRun
    ) -> dict[str, Any] | None:
        suite = await session.get(EvaluationSuite, run.evaluation_suite_id)
        if suite is None:
            return None
        budgets = suite.thresholds.get("budgets")
        return budgets if isinstance(budgets, dict) else None

    async def _fail_item(
        self,
        session: AsyncSession,
        *,
        item: RunItem,
        trajectory: Trajectory,
        attempt: int,
        retryable: bool,
        error_code: str,
        error_message: str,
        fault_payload: dict[str, Any] | None,
        budget_state: dict[str, Any],
        title: str,
    ) -> None:
        """End one attempt in failure, recording why in the trajectory.

        A retryable failure goes straight back to ``PENDING``. The state machine
        allows ``FAILED_RETRYABLE`` as a resting place, but parking there would
        need a second sweep to wake it, and the run loop is already looking for
        pending work — so the retry budget is spent by the lease, not by a timer.
        """
        await self._append_step(
            session,
            trajectory_id=trajectory.id,
            step_index=_FAULT_STEP_BASE + attempt,
            step_type=TrajectoryStepType.FINAL_RESULT,
            title=title,
            input_payload={"attempt": attempt, "item_index": item.item_index},
            output_payload={
                "passed": False,
                "error_code": error_code,
                "retryable": retryable,
                "fault": fault_payload,
            },
            evidence={"budget": budget_state},
            checkpoint={"stage": "failed", "attempt": attempt, "error_code": error_code},
        )
        next_state = RunItemState.PENDING if retryable else RunItemState.FAILED_TERMINAL
        await session.execute(
            update(RunItem)
            .where(RunItem.id == item.id, RunItem.state == RunItemState.EVALUATING)
            .values(
                state=next_state,
                error_code=error_code,
                error_message=error_message,
                injected_fault=fault_payload,
                budget_state=budget_state,
                worker_id=None,
                lease_expires_at=None,
                completed_at=None if retryable else func.now(),
                updated_at=func.now(),
                version=RunItem.version + 1,
            )
        )
        if retryable:
            return
        trajectory.state = TrajectoryState.FAILED
        trajectory.summary = {
            **trajectory.summary,
            "result": "failed",
            "error_code": error_code,
            "failing_step_id": None,
            "attempts": attempt,
        }
        trajectory.graph_state = {
            "node": "recorded_executor",
            "state": "failed",
            "item_index": item.item_index,
        }
        trajectory.final_checkpoint = {"stage": "failed", "error_code": error_code}
        trajectory.completed_at = datetime.now(UTC)
        trajectory.updated_at = trajectory.completed_at

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
            comparison = await self._build_comparison_report(session, run=run)
            run.state = EvaluationRunState.PASSED if failed == 0 else EvaluationRunState.FAILED
            run.summary = {**run.summary, "comparison_report_id": comparison.id}
            run.completed_at = datetime.now(UTC)
            run.updated_at = run.completed_at
            run.version += 1
            await session.commit()
        return RunOutcome.PASSED if failed == 0 else RunOutcome.FAILED

    async def _build_comparison_report(
        self, session: AsyncSession, *, run: EvaluationRun
    ) -> ComparisonReport:
        existing = await session.scalar(
            select(ComparisonReport).where(ComparisonReport.run_id == run.id).with_for_update()
        )
        if existing is not None:
            return existing
        suite = await session.get(EvaluationSuite, run.evaluation_suite_id)
        evaluators = default_evaluators(suite.evaluators if suite is not None else [])
        suite_digest = canonical_digest(
            {
                "evaluation_suite_id": run.evaluation_suite_id,
                "evaluators": evaluators,
                "thresholds": suite.thresholds if suite is not None else {},
            }
        )
        items = list(
            (
                await session.scalars(
                    select(RunItem).where(RunItem.run_id == run.id).order_by(RunItem.item_index)
                )
            ).all()
        )
        results: list[EvaluationResult] = []
        for item in items:
            for evaluator in evaluators:
                evaluator_version = await self._ensure_evaluator_version(
                    session, project_id=run.project_id, evaluator=evaluator
                )
                state, score, details = score_run_item(
                    item_state=item.state, item_result=item.result, evaluator=evaluator
                )
                result = EvaluationResult(
                    id=new_sortable_id(),
                    run_id=run.id,
                    run_item_id=item.id,
                    evaluator_version_id=evaluator_version.id,
                    evaluator_slug=str(evaluator["slug"]),
                    evaluator_kind=EvaluatorKind(str(evaluator["kind"])),
                    item_index=item.item_index,
                    partition=item.partition,
                    category=str(evaluator["category"]),
                    state=state,
                    score=score,
                    threshold=float(evaluator["threshold"]),
                    details=details,
                )
                session.add(result)
                results.append(result)
        await session.flush()
        summary, evaluator_metrics, category_metrics, regressions = aggregate_results(
            item_count=run.item_count, results=results
        )
        report = ComparisonReport(
            id=new_sortable_id(),
            project_id=run.project_id,
            run_id=run.id,
            baseline_agent_version_id=run.baseline_agent_version_id,
            candidate_agent_version_id=run.candidate_agent_version_id,
            suite_digest=suite_digest,
            summary=summary,
            evaluator_metrics=evaluator_metrics,
            category_metrics=category_metrics,
            regressions=regressions,
            exports={
                "json": f"agentrail://evaluation-runs/{run.id}/comparison",
                "csv": f"agentrail://evaluation-runs/{run.id}/evaluator-results.csv",
            },
        )
        session.add(report)
        await session.flush()
        return report

    async def _ensure_evaluator_version(
        self, session: AsyncSession, *, project_id: str, evaluator: dict[str, object]
    ) -> EvaluatorVersion:
        definition_digest = canonical_digest(evaluator)
        existing = await session.scalar(
            select(EvaluatorVersion).where(
                EvaluatorVersion.project_id == project_id,
                EvaluatorVersion.definition_digest == definition_digest,
            )
        )
        if existing is not None:
            return existing
        slug = str(evaluator["slug"])
        latest_version = await session.scalar(
            select(func.max(EvaluatorVersion.version)).where(
                EvaluatorVersion.project_id == project_id,
                EvaluatorVersion.slug == slug,
            )
        )
        version = EvaluatorVersion(
            id=new_sortable_id(),
            project_id=project_id,
            slug=slug,
            version=int(latest_version or 0) + 1,
            kind=EvaluatorKind(str(evaluator["kind"])),
            name=str(evaluator["name"]),
            definition=dict(evaluator),
            definition_digest=definition_digest,
        )
        session.add(version)
        await session.flush()
        return version

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
