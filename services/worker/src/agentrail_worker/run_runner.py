"""Claim and execute durable evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.approvals import ApprovalRequest, ApprovalState
from agentrail_core.datasets import EvaluationSuite
from agentrail_core.db import set_tenant_context
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
from agentrail_core.identity import AgentVersion, Project
from agentrail_core.ids import new_sortable_id
from agentrail_core.logging import get_logger
from agentrail_core.policy import (
    PolicyBundle,
    PolicyDecision,
    ToolRiskLevel,
    decide,
    escalates,
    parse_policy_bundle,
)
from agentrail_core.reliability import BudgetExceededError, BudgetKind, BudgetLedger
from agentrail_core.side_effects import apply_side_effect_once, side_effect_key
from agentrail_core.trajectories import (
    Trajectory,
    TrajectoryCheckpoint,
    TrajectoryState,
    TrajectoryStep,
    TrajectoryStepType,
    redact_payload,
)
from agentrail_core.tribunal import (
    TribunalMode,
    TribunalVerdictOutcome,
    build_tribunal_model_client,
    create_or_get_tribunal_session,
    tribunal_enabled,
    validate_tribunal_config,
)
from agentrail_worker.langgraph_executor import (
    ApprovalPending,
    CapturedEvent,
    GraphSpecError,
    LangGraphExecutor,
    ToolInvocation,
    ToolResult,
)

logger = get_logger(__name__)
_TRIBUNAL_CONDITIONAL_APPROVAL_TOOL = "tribunal_conditional_release"
_TRIBUNAL_CONDITIONAL_APPROVAL_STEP = 9000
_TRIBUNAL_TRAJECTORY_LINK_LIMIT = 10


async def _set_run_tenant_context(session: AsyncSession, run: EvaluationRun) -> None:
    organisation_id = await session.scalar(
        select(Project.organisation_id).where(Project.id == run.project_id)
    )
    if organisation_id is not None:
        await set_tenant_context(session, organisation_id)


class RunOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    MISSING = "missing"


def _aggregate_run_state(*, failed_count: int, tribunal_outcome: str | None) -> EvaluationRunState:
    if tribunal_outcome == TribunalVerdictOutcome.BLOCKED.value:
        return EvaluationRunState.FAILED
    if tribunal_outcome == TribunalVerdictOutcome.CONDITIONAL.value:
        return EvaluationRunState.PASSED
    if failed_count > 0:
        return EvaluationRunState.FAILED
    return EvaluationRunState.PASSED


def _tribunal_gate_summary(outcome: str) -> dict[str, Any]:
    if outcome == TribunalVerdictOutcome.BLOCKED.value:
        return {
            "effect": "blocked_run",
            "requires_human_approval": False,
            "message": "Tribunal blocked the run, so Round 4 forced the run to FAILED.",
        }
    if outcome == TribunalVerdictOutcome.CONDITIONAL.value:
        return {
            "effect": "passed_with_warnings",
            "requires_human_approval": True,
            "message": (
                "Tribunal returned a conditional verdict, so the run passed with "
                "warnings and requires human approval before release."
            ),
        }
    return {
        "effect": "approved",
        "requires_human_approval": False,
        "message": "Tribunal approved the run.",
    }


async def _attach_trajectory_step_links(
    session: AsyncSession, regressions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    run_item_ids: list[str] = []
    seen: set[str] = set()
    for regression in regressions:
        run_item_id = regression.get("run_item_id")
        if isinstance(run_item_id, str) and run_item_id not in seen:
            seen.add(run_item_id)
            run_item_ids.append(run_item_id)
        if len(run_item_ids) >= _TRIBUNAL_TRAJECTORY_LINK_LIMIT:
            break

    if not run_item_ids:
        return regressions

    trajectories = list(
        (
            await session.scalars(
                select(Trajectory).where(Trajectory.run_item_id.in_(run_item_ids))
            )
        ).all()
    )
    trajectories_by_item = {trajectory.run_item_id: trajectory for trajectory in trajectories}
    trajectory_ids = [trajectory.id for trajectory in trajectories]
    if not trajectory_ids:
        return regressions

    preferred_steps = list(
        (
            await session.scalars(
                select(TrajectoryStep)
                .where(TrajectoryStep.trajectory_id.in_(trajectory_ids))
                .where(
                    TrajectoryStep.step_type.in_(
                        [TrajectoryStepType.ERROR, TrajectoryStepType.FINAL_RESULT]
                    )
                )
                .order_by(TrajectoryStep.trajectory_id, TrajectoryStep.step_index.desc())
            )
        ).all()
    )
    steps_by_trajectory = _select_trajectory_steps(preferred_steps)
    missing_trajectory_ids = [
        trajectory_id
        for trajectory_id in trajectory_ids
        if trajectory_id not in steps_by_trajectory
    ]
    if missing_trajectory_ids:
        fallback_steps = list(
            (
                await session.scalars(
                    select(TrajectoryStep)
                    .where(TrajectoryStep.trajectory_id.in_(missing_trajectory_ids))
                    .order_by(TrajectoryStep.trajectory_id, TrajectoryStep.step_index.desc())
                )
            ).all()
        )
        steps_by_trajectory.update(_select_trajectory_steps(fallback_steps))

    links_by_item: dict[str, dict[str, Any]] = {}
    for run_item_id, trajectory in trajectories_by_item.items():
        step = steps_by_trajectory.get(trajectory.id)
        if step is None:
            continue
        links_by_item[run_item_id] = _trajectory_step_payload(trajectory, step)

    enriched: list[dict[str, Any]] = []
    for regression in regressions:
        linked = dict(regression)
        run_item_id = regression.get("run_item_id")
        if isinstance(run_item_id, str) and run_item_id in links_by_item:
            linked["trajectory_step"] = links_by_item[run_item_id]
        enriched.append(linked)
    return enriched


def _select_trajectory_steps(steps: list[TrajectoryStep]) -> dict[str, TrajectoryStep]:
    selected: dict[str, TrajectoryStep] = {}
    for step in steps:
        current = selected.get(step.trajectory_id)
        if current is None or _trajectory_step_rank(step) > _trajectory_step_rank(current):
            selected[step.trajectory_id] = step
    return selected


def _trajectory_step_rank(step: TrajectoryStep) -> tuple[int, int]:
    step_type = _enum_value(step.step_type)
    preferred = 2 if step_type == TrajectoryStepType.ERROR.value else 1
    return (preferred, step.step_index)


def _trajectory_step_payload(trajectory: Trajectory, step: TrajectoryStep) -> dict[str, Any]:
    return {
        "trajectory_id": trajectory.id,
        "trajectory_state": _enum_value(trajectory.state),
        "step_id": step.id,
        "step_index": step.step_index,
        "step_type": _enum_value(step.step_type),
        "title": step.title,
    }


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


async def _ensure_tribunal_conditional_approval(
    session: AsyncSession, *, run: EvaluationRun, tribunal_session_id: str
) -> ApprovalRequest:
    item = await session.scalar(
        select(RunItem).where(RunItem.run_id == run.id).order_by(RunItem.item_index).limit(1)
    )
    if item is None:  # pragma: no cover - runs always own at least one item
        raise RuntimeError("Cannot create Tribunal approval for a run with no items.")
    trajectory_id = await session.scalar(
        select(Trajectory.id).where(Trajectory.run_item_id == item.id).limit(1)
    )
    arguments = {
        "tribunal_session_id": tribunal_session_id,
        "outcome": TribunalVerdictOutcome.CONDITIONAL.value,
        "action": "approve_conditional_release",
    }
    key = side_effect_key(
        run_item_id=item.id,
        step_index=_TRIBUNAL_CONDITIONAL_APPROVAL_STEP,
        tool=_TRIBUNAL_CONDITIONAL_APPROVAL_TOOL,
        arguments=arguments,
    )
    existing = await session.scalar(
        select(ApprovalRequest).where(ApprovalRequest.idempotency_key == key).with_for_update()
    )
    if existing is not None:
        return existing
    request = ApprovalRequest(
        id=new_sortable_id(),
        project_id=run.project_id,
        run_id=run.id,
        run_item_id=item.id,
        trajectory_id=trajectory_id,
        idempotency_key=key,
        tool=_TRIBUNAL_CONDITIONAL_APPROVAL_TOOL,
        risk_level=ToolRiskLevel.HIGH_RISK_WRITE.value,
        state=ApprovalState.PENDING,
        requested_arguments=arguments,
        reason="Tribunal returned a conditional verdict; release requires human approval.",
    )
    try:
        async with session.begin_nested():
            session.add(request)
            await session.flush()
    except IntegrityError:
        winner = await session.scalar(
            select(ApprovalRequest).where(ApprovalRequest.idempotency_key == key).with_for_update()
        )
        if winner is None:  # pragma: no cover - only reachable if the row vanished
            raise
        return winner
    return request


#: Attempts share one trajectory — it is keyed by run item, not by attempt — so
#: fault steps need an index band of their own that the clean-path steps (0-5)
#: can never collide with, however many times an item is retried.
_FAULT_STEP_BASE = 50

#: The one side-effecting call the recorded executor makes. Named after the
#: CloudOps remediation tool it stands in for, so the ledger reads the way it
#: will once a real agent runtime is choosing the tool.
_REMEDIATION_TOOL = "restart_service"
#: model_config.provider value that selects the LangGraph execution path.
_LANGGRAPH_PROVIDER = "langgraph"

#: Its own band again, above the fault steps, for the same reason: attempts
#: share a trajectory, and an item can park for approval more than once.
_APPROVAL_STEP_BASE = 70


@dataclass(frozen=True, slots=True)
class PolicyGate:
    """The outcome of asking policy whether a tool call may proceed."""

    arguments: dict[str, Any]
    #: The caller has nothing left to do — the item is parked or terminal.
    halted: bool = False
    required_approval: bool = False
    approval_id: str | None = None
    #: Set only when the halt is a pending human decision, which is the one
    #: halt a later attempt can resume from rather than restart.
    parked_approval_id: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimedRun:
    id: str
    item_count: int


class _HaltedByPlatform(Exception):
    """Policy, budget or approval stopped the graph. The item is already settled."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _RunnerToolGateway:
    """The ToolGateway a LangGraph run calls instead of touching a tool.

    Every guarantee the recorded path enforces inline is enforced here, in the
    same order and for the same reason: charge the budget, ask policy, then
    apply the effect through the idempotent ledger. The effect reaches the world
    before any fault can kill the attempt, so a retry finds the ledger row and
    declines to act a second time.

    A graph cannot opt out of this, because it has no other way to call a tool.
    """

    def __init__(
        self,
        runner: EvaluationRunRunner,
        session: AsyncSession,
        *,
        run: EvaluationRun,
        item: RunItem,
        trajectory: Trajectory,
        attempt: int,
        ledger: BudgetLedger,
    ) -> None:
        self._runner = runner
        self._session = session
        self._run = run
        self._item = item
        self._trajectory = trajectory
        self._attempt = attempt
        self.ledger = ledger
        #: Step 1 is the graph-state step the runner writes before streaming.
        self._step_index = 1
        self.applied: list[dict[str, Any]] = []

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self._step_index += 1
        step_index = self._step_index
        try:
            self.ledger = self.ledger.charge(BudgetKind.TOOL_CALLS, 1)
            self.ledger = self.ledger.charge(BudgetKind.LOOP_ITERATIONS, 1)
        except BudgetExceededError as exceeded:
            await self._runner._fail_item(
                self._session,
                item=self._item,
                trajectory=self._trajectory,
                attempt=self._attempt,
                retryable=False,
                error_code="budget_exhausted",
                error_message=str(exceeded),
                fault_payload=None,
                budget_state=exceeded.ledger.as_payload(),
                title=f"Budget exhausted: {exceeded.kind.value}",
            )
            raise _HaltedByPlatform("budget_exhausted") from exceeded

        gate = await self._runner._policy_gate(
            self._session,
            run=self._run,
            item=self._item,
            trajectory=self._trajectory,
            attempt=self._attempt,
            arguments=dict(invocation.arguments),
            budget_state=self.ledger.as_payload(),
            tool=invocation.tool,
            step_index=step_index,
        )
        if gate.parked_approval_id is not None:
            # A pending decision is not a failure. Raising ApprovalPending lets
            # the node checkpoint an interrupt, so the human's answer resumes
            # this exact node instead of replaying the graph from the start.
            raise ApprovalPending(gate.parked_approval_id, invocation.tool)
        if gate.halted:
            raise _HaltedByPlatform("policy_gate")

        record, was_applied = await apply_side_effect_once(
            self._session,
            project_id=self._run.project_id,
            run_id=self._run.id,
            run_item_id=self._item.id,
            step_index=step_index,
            tool=invocation.tool,
            arguments=gate.arguments,
            attempt=self._attempt,
            result={"status": "ok", "tool": invocation.tool},
            required_approval=gate.required_approval,
            approval_id=gate.approval_id,
        )
        output: dict[str, Any] = {
            "status": "ok",
            "side_effect_applied": was_applied,
            "idempotent_replay": not was_applied,
            "applied_on_attempt": record.applied_on_attempt,
        }
        self.applied.append({"tool": invocation.tool, "step_index": step_index, **output})
        return ToolResult(tool=invocation.tool, output=output, latency_ms=0)


class EvaluationRunRunner:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        worker_id: str,
        lease_seconds: float = 30.0,
        openai_api_key: str | None = None,
        openai_base_url: str = "https://api.openai.com/v1",
        tribunal_model_timeout_seconds: float = 60.0,
        database_url: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._openai_api_key = openai_api_key
        self._openai_base_url = openai_base_url
        self._tribunal_model_timeout_seconds = tribunal_model_timeout_seconds
        # Absent without a database URL. An agent version that asks for
        # LangGraph then fails its own item rather than the worker, so a
        # misconfigured deployment cannot take down runs that do not use it.
        self._langgraph = (
            LangGraphExecutor(database_url=database_url) if database_url is not None else None
        )

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
            await _set_run_tenant_context(session, run)
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
            if await self._uses_langgraph(session, run=run):
                await self._execute_langgraph_item(session, run=run, item=item)
            else:
                await self._execute_recorded_item(session, run=run, item=item)

    async def _uses_langgraph(self, session: AsyncSession, *, run: EvaluationRun) -> bool:
        """Whether this run's candidate version asks to execute under LangGraph.

        Recorded is the default and stays the default: only a version that names
        the langgraph provider takes the new path, so existing runs, CI and the
        frozen benchmark are unaffected.
        """
        if self._langgraph is None:
            return False
        version = await session.get(AgentVersion, run.candidate_agent_version_id)
        if version is None:
            return False
        provider = version.model_config.get("provider")
        return isinstance(provider, str) and provider == _LANGGRAPH_PROVIDER

    async def _execute_recorded_item(
        self, session: AsyncSession, *, run: EvaluationRun, item: RunItem
    ) -> None:
        """Execute one item on the deterministic recorded path.

        Extracted verbatim from ``_execute_item`` so a second execution path
        can sit beside it. The caller owns claiming the item and committing;
        everything from trajectory creation onward belongs here.
        """
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

        # Policy runs before anything reaches the world. A denial or an
        # unanswered approval stops the attempt here, with the effect
        # unapplied — which is the only ordering that makes the gate real.
        gate = await self._policy_gate(
            session,
            run=run,
            item=item,
            trajectory=trajectory,
            attempt=attempt,
            arguments=arguments,
            budget_state=ledger.as_payload(),
        )
        if gate.halted:
            await session.commit()
            return
        arguments = gate.arguments

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
            required_approval=gate.required_approval,
            approval_id=gate.approval_id,
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

    async def _execute_langgraph_item(
        self, session: AsyncSession, *, run: EvaluationRun, item: RunItem
    ) -> None:
        """Execute one item by running the agent version's graph under LangGraph.

        The platform guarantees live above the graph, not inside it: the item is
        already claimed, the gateway owns budgets/policy/idempotency, and this
        method owns the trajectory and the item's terminal state. The graph only
        decides what to attempt.
        """
        version = await session.get(AgentVersion, run.candidate_agent_version_id)
        trajectory = await self._create_trajectory(session, item=item, run=run)
        graph_step = await self._append_step(
            session,
            trajectory_id=trajectory.id,
            step_index=1,
            step_type=TrajectoryStepType.GRAPH_STATE,
            title="Load graph state",
            input_payload={"item_index": item.item_index, "partition": item.partition},
            output_payload={
                "node": "langgraph",
                "state": "ready",
                "candidate_agent_version_id": run.candidate_agent_version_id,
            },
            checkpoint={"stage": "graph_state", "node": "langgraph"},
        )
        await self._append_checkpoint(
            session,
            trajectory_id=trajectory.id,
            step_id=graph_step.id,
            checkpoint_index=0,
            label="graph-state-ready",
            state=graph_step.checkpoint,
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
        ledger = BudgetLedger.restore(
            await self._budget_limits(session, run=run), item.budget_state
        )
        gateway = _RunnerToolGateway(
            self,
            session,
            run=run,
            item=item,
            trajectory=trajectory,
            attempt=attempt,
            ledger=ledger,
        )

        assert self._langgraph is not None, "dispatch checked this"
        # A halt unwinds the graph by exception, so events are collected as they
        # happen. Otherwise a denied third tool call would erase the trace of the
        # two that already ran, which is the opposite of what a reviewer needs.
        captured: list[CapturedEvent] = []
        try:
            outcome = await self._langgraph.execute(
                graph_spec=version.graph_spec if version is not None else {},
                gateway=gateway,
                # One thread per run item, so two items in a run never share
                # graph state and a resumed item finds its own.
                thread_id=f"run-item-{item.id}",
                item_index=item.item_index,
                partition=item.partition,
                sink=captured,
                # Releases a saved approval interrupt. Must be non-None; the
                # node ignores the value and re-reads the approval from the
                # database, which is the authority on the decision.
                resume_value={"released": True, "attempt": attempt},
            )
        except _HaltedByPlatform:
            # The gateway already parked or failed the item and recorded why.
            # Whatever ran before the halt still belongs in the trace.
            await self._record_captured_events(
                session,
                trajectory=trajectory,
                events=captured,
                start_index=await self._last_step_index(session, trajectory=trajectory),
            )
            await session.commit()
            return
        except GraphSpecError as invalid:
            # A version whose graph cannot compile fails its item rather than
            # the worker, exactly as an unexecutable fault profile does.
            await self._fail_item(
                session,
                item=item,
                trajectory=trajectory,
                attempt=attempt,
                retryable=False,
                error_code="graph_spec_invalid",
                error_message=str(invalid),
                fault_payload=None,
                budget_state=gateway.ledger.as_payload(),
                title="Unexecutable graph spec",
            )
            await session.commit()
            return

        step_index = await self._record_captured_events(
            session,
            trajectory=trajectory,
            events=outcome.events,
            start_index=await self._last_step_index(session, trajectory=trajectory),
        )
        if outcome.interrupted_on is not None:
            # The gateway already parked the item and recorded the approval.
            # The graph is checkpointed mid-flight; the next attempt resumes it.
            await self._append_step(
                session,
                trajectory_id=trajectory.id,
                step_index=step_index + 1,
                step_type=TrajectoryStepType.CHECKPOINT,
                title="Awaiting approval",
                input_payload={"thread_id": outcome.thread_id},
                output_payload=outcome.interrupted_on,
                checkpoint={"stage": "awaiting_approval", **outcome.interrupted_on},
            )
            await session.commit()
            return
        final_step = await self._append_step(
            session,
            trajectory_id=trajectory.id,
            step_index=step_index + 1,
            step_type=TrajectoryStepType.FINAL_RESULT,
            title="LangGraph final result",
            input_payload={"thread_id": outcome.thread_id},
            output_payload={"passed": outcome.passed, "mode": "langgraph"},
            evidence={"graph_state": outcome.final_state},
            checkpoint={"stage": "final_result", "passed": outcome.passed},
        )
        await self._append_checkpoint(
            session,
            trajectory_id=trajectory.id,
            step_id=final_step.id,
            checkpoint_index=1,
            label="item-completed",
            state=final_step.checkpoint,
        )

        trajectory.state = TrajectoryState.COMPLETED
        trajectory.summary = {
            "step_count": step_index + 1,
            "checkpoint_count": 2,
            "result": "passed" if outcome.passed else "failed",
            "failing_step_id": None,
        }
        trajectory.graph_state = outcome.final_state
        trajectory.final_checkpoint = final_step.checkpoint
        trajectory.completed_at = datetime.now(UTC)
        trajectory.updated_at = trajectory.completed_at
        await session.execute(
            update(RunItem)
            .where(RunItem.id == item.id, RunItem.state == RunItemState.EVALUATING)
            .values(
                state=RunItemState.COMPLETED if outcome.passed else RunItemState.FAILED_TERMINAL,
                result={
                    "passed": outcome.passed,
                    "mode": "langgraph",
                    "trajectory_id": trajectory.id,
                    "tool_calls": gateway.applied,
                },
                checkpoint={
                    "stage": "completed",
                    "item_index": item.item_index,
                    "trajectory_id": trajectory.id,
                },
                injected_fault=None,
                budget_state=gateway.ledger.as_payload(),
                lease_expires_at=None,
                completed_at=func.now(),
                updated_at=func.now(),
                version=RunItem.version + 1,
            )
        )
        await session.commit()

    async def _last_step_index(self, session: AsyncSession, *, trajectory: Trajectory) -> int:
        """The highest step index already recorded for this trajectory.

        A resumed item appends to a trajectory that already holds rows —
        the pre-approval transitions and the awaiting-approval checkpoint.
        Restarting the numbering would collide with them on the
        (trajectory_id, step_index) unique constraint and lose the approved
        tool transition from the audit trail.
        """
        highest = await session.scalar(
            select(func.max(TrajectoryStep.step_index)).where(
                TrajectoryStep.trajectory_id == trajectory.id
            )
        )
        return int(highest) if highest is not None else 1

    async def _record_captured_events(
        self,
        session: AsyncSession,
        *,
        trajectory: Trajectory,
        events: list[CapturedEvent],
        start_index: int,
    ) -> int:
        """Turn each captured node transition into one trajectory step.

        This is what makes per-step graph state real for LangGraph runs: the
        state stored is the state *as of that node*, not one end-of-run snapshot
        repeated on every row.
        """
        step_index = start_index
        for event in events:
            step_index += 1
            step_type = (
                TrajectoryStepType.TOOL_CALL
                if event.kind == "tool_call"
                else TrajectoryStepType.EVIDENCE
                if event.kind == "evidence"
                else TrajectoryStepType.GRAPH_STATE
            )
            await self._append_step(
                session,
                trajectory_id=trajectory.id,
                step_index=step_index,
                step_type=step_type,
                title=f"{event.node} ({event.kind})",
                input_payload={"node": event.node},
                output_payload={"kind": event.kind},
                checkpoint=event.state,
                evidence={"items": event.state.get("evidence", [])}
                if event.kind == "evidence"
                else None,
            )
        return step_index

    async def _policy_gate(
        self,
        session: AsyncSession,
        *,
        run: EvaluationRun,
        item: RunItem,
        trajectory: Trajectory,
        attempt: int,
        arguments: dict[str, Any],
        budget_state: dict[str, Any],
        tool: str = _REMEDIATION_TOOL,
        step_index: int = 2,
    ) -> PolicyGate:
        """Decide whether this tool call may proceed, and on what arguments.

        Returns a gate that either lets the caller continue or has already
        parked or failed the item. Nothing here applies an effect.

        ``tool`` and ``step_index`` default to the recorded path's single
        remediation call. A graph-driven run passes its own, so the approval
        key stays unique per intended effect rather than per item.
        """
        bundle = await self._policy_bundle(session, run=run)
        verdict, risk = decide(bundle, tool=tool)

        if verdict is PolicyDecision.ALLOW:
            return PolicyGate(arguments=arguments)

        if verdict is PolicyDecision.DENY:
            await self._fail_item(
                session,
                item=item,
                trajectory=trajectory,
                attempt=attempt,
                retryable=False,
                error_code="policy_denied",
                error_message=f"Policy prohibits {tool}.",
                fault_payload=None,
                budget_state=budget_state,
                title=f"Policy denied: {tool}",
            )
            return PolicyGate(arguments=arguments, halted=True)

        # REQUIRE_APPROVAL. The key is the same one the ledger will use, so the
        # question is asked once per intended effect rather than once per
        # attempt — a retried or redelivered item finds its own request here.
        key = side_effect_key(
            run_item_id=item.id, step_index=step_index, tool=tool, arguments=arguments
        )
        if await self._would_escalate(session, bundle=bundle, item=item, key=key):
            # Terminal, and deliberately distinct from a denial: this call was
            # approvable on an earlier ask, and an operator reading the trail
            # has to be able to tell those two apart.
            await self._fail_item(
                session,
                item=item,
                trajectory=trajectory,
                attempt=attempt,
                retryable=False,
                error_code="approval_escalated",
                error_message=(
                    f"Approval for {tool} escalated after "
                    f"{bundle.escalate_after_attempts} request(s) on this item."
                ),
                fault_payload=None,
                budget_state=budget_state,
                title=f"Approval escalated: {tool}",
            )
            return PolicyGate(arguments=arguments, halted=True)

        approval = await self._approval_for(
            session,
            run=run,
            item=item,
            trajectory=trajectory,
            key=key,
            risk=risk,
            arguments=arguments,
            tool=tool,
        )

        # The column is a plain string with a check constraint, as every other
        # state column in this schema is, so a row read back carries a ``str``
        # rather than the enum its annotation promises. Coerce once, here, so
        # the branches below can use identity and ``.value`` safely.
        state = ApprovalState(approval.state)

        if state is ApprovalState.APPROVED:
            # The reviewer's edit replaces the arguments, which changes the
            # ledger key: a different action is a different effect, and must not
            # inherit the authorisation recorded for the original.
            return PolicyGate(
                arguments=dict(approval.effective_arguments),
                required_approval=True,
                approval_id=approval.id,
            )

        if state is ApprovalState.PENDING:
            await self._park_for_approval(
                session,
                item=item,
                trajectory=trajectory,
                attempt=attempt,
                approval=approval,
                budget_state=budget_state,
            )
            return PolicyGate(arguments=arguments, halted=True, parked_approval_id=approval.id)

        # REJECTED or WITHDRAWN. Both are terminal for the approval and there is
        # no edge back to PENDING, so a delayed delivery arriving after the
        # decision lands here too and stops just the same.
        await self._fail_item(
            session,
            item=item,
            trajectory=trajectory,
            attempt=attempt,
            retryable=False,
            error_code="approval_rejected",
            error_message=f"Approval {approval.id} is {state.value}.",
            fault_payload=None,
            budget_state=budget_state,
            title=f"Approval {state.value.lower()}",
        )
        return PolicyGate(arguments=arguments, halted=True)

    async def _would_escalate(
        self, session: AsyncSession, *, bundle: PolicyBundle, item: RunItem, key: str
    ) -> bool:
        """Whether raising another approval request should be refused.

        Only a *new* request escalates. If this exact effect already has a
        request, it is answered by its own decision — escalating there would
        block something a human had already approved.
        """
        if bundle.escalate_after_attempts is None:
            return False
        existing = await session.scalar(
            select(ApprovalRequest.id).where(ApprovalRequest.idempotency_key == key)
        )
        if existing is not None:
            return False
        prior = await session.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.run_item_id == item.id)
        )
        return escalates(bundle, prior_asks=int(prior or 0))

    async def _approval_for(
        self,
        session: AsyncSession,
        *,
        run: EvaluationRun,
        item: RunItem,
        trajectory: Trajectory,
        key: str,
        risk: ToolRiskLevel,
        arguments: dict[str, Any],
        tool: str = _REMEDIATION_TOOL,
    ) -> ApprovalRequest:
        """Find this effect's approval request, creating it the first time.

        Locked for update, because the decision is read and acted on in the same
        transaction that writes the ledger row. Without the lock a reviewer
        could reject between the read and the insert, and the effect would land
        against a decision that had already been made.
        """
        existing = await session.scalar(
            select(ApprovalRequest).where(ApprovalRequest.idempotency_key == key).with_for_update()
        )
        if existing is not None:
            return existing

        redacted_arguments, _summary = redact_payload(arguments)
        request = ApprovalRequest(
            id=new_sortable_id(),
            project_id=run.project_id,
            run_id=run.id,
            run_item_id=item.id,
            trajectory_id=trajectory.id,
            idempotency_key=key,
            tool=tool,
            risk_level=risk.value,
            state=ApprovalState.PENDING,
            requested_arguments=redacted_arguments,
        )
        try:
            async with session.begin_nested():
                session.add(request)
                await session.flush()
        except IntegrityError:
            winner = await session.scalar(
                select(ApprovalRequest)
                .where(ApprovalRequest.idempotency_key == key)
                .with_for_update()
            )
            if winner is None:  # pragma: no cover - only reachable if the row vanished
                raise
            return winner
        return request

    async def _park_for_approval(
        self,
        session: AsyncSession,
        *,
        item: RunItem,
        trajectory: Trajectory,
        attempt: int,
        approval: ApprovalRequest,
        budget_state: dict[str, Any],
    ) -> None:
        """Park the item on a human, holding no lease and burning no retry.

        The lease is released because a reviewer is not on a worker's clock —
        leaving it held would have the recovery sweep reclaim the item and hand
        it to another worker, which would park it again, forever.
        """
        await self._append_step(
            session,
            trajectory_id=trajectory.id,
            step_index=_APPROVAL_STEP_BASE + attempt,
            step_type=TrajectoryStepType.CHECKPOINT,
            title=f"Awaiting approval: {approval.tool}",
            input_payload={"tool": approval.tool, "arguments": approval.requested_arguments},
            output_payload={
                "approval_id": approval.id,
                "risk_level": approval.risk_level,
                "state": ApprovalState(approval.state).value,
            },
            checkpoint={
                "stage": "awaiting_approval",
                "approval_id": approval.id,
                "attempt": attempt,
            },
        )
        await session.execute(
            update(RunItem)
            .where(RunItem.id == item.id, RunItem.state == RunItemState.EVALUATING)
            .values(
                state=RunItemState.AWAITING_APPROVAL,
                checkpoint={
                    "stage": "awaiting_approval",
                    "item_index": item.item_index,
                    "trajectory_id": trajectory.id,
                    "approval_id": approval.id,
                },
                budget_state=budget_state,
                worker_id=None,
                lease_expires_at=None,
                # Waiting on a human is not an attempt. Charging one would let a
                # slow reviewer exhaust the retry budget on the item's behalf.
                attempt_count=RunItem.attempt_count - 1,
                updated_at=func.now(),
                version=RunItem.version + 1,
            )
        )

    async def _policy_bundle(self, session: AsyncSession, *, run: EvaluationRun) -> PolicyBundle:
        version = await session.get(AgentVersion, run.candidate_agent_version_id)
        if version is None:
            return PolicyBundle()
        return parse_policy_bundle(version.policy_bundle)

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
            run = await session.scalar(
                select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update()
            )
            if run is None:
                return RunOutcome.MISSING
            await _set_run_tenant_context(session, run)
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
            suite = await session.get(EvaluationSuite, run.evaluation_suite_id)
            tribunal_summary: dict[str, Any] = {}
            tribunal_outcome: str | None = None
            if suite is not None and tribunal_enabled(suite.thresholds):
                tribunal_config = suite.thresholds.get("tribunal")
                model_client = None
                if tribunal_config is not None:
                    parsed_tribunal_config = validate_tribunal_config({"tribunal": tribunal_config})
                    if parsed_tribunal_config["mode"] == TribunalMode.MODEL_BACKED.value:
                        model_client = build_tribunal_model_client(
                            parsed_tribunal_config,
                            openai_api_key=self._openai_api_key,
                            openai_base_url=self._openai_base_url,
                            timeout_seconds=self._tribunal_model_timeout_seconds,
                        )
                tribunal, _created = await create_or_get_tribunal_session(
                    session,
                    run=run,
                    comparison=comparison,
                    tribunal_config=tribunal_config,
                    model_client=model_client,
                )
                tribunal_summary = {
                    "tribunal_session_id": tribunal.session.id,
                    "tribunal_outcome": tribunal.session.outcome.value,
                }
                tribunal_outcome = tribunal.session.outcome.value
                tribunal_summary["tribunal_gate"] = _tribunal_gate_summary(tribunal_outcome)
                if tribunal_outcome == TribunalVerdictOutcome.CONDITIONAL.value:
                    approval = await _ensure_tribunal_conditional_approval(
                        session, run=run, tribunal_session_id=tribunal.session.id
                    )
                    tribunal_summary["tribunal_conditional_approval_id"] = approval.id
                    tribunal_summary["tribunal_conditional_approval_state"] = _enum_value(
                        approval.state
                    )
            final_state = _aggregate_run_state(
                failed_count=failed, tribunal_outcome=tribunal_outcome
            )
            run.state = final_state
            run.summary = {**run.summary, "comparison_report_id": comparison.id, **tribunal_summary}
            run.completed_at = datetime.now(UTC)
            run.updated_at = run.completed_at
            run.version += 1
            await session.commit()
        return RunOutcome.PASSED if final_state is EvaluationRunState.PASSED else RunOutcome.FAILED

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
        regressions = await _attach_trajectory_step_links(session, regressions)
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
                        # A resume is published the same way a creation is: an
                        # approved item is pending work again, and the consumer
                        # cannot tell the difference — nor should it need to.
                        OutboxEvent.event_type.in_(
                            ("evaluation_run.created", "evaluation_run.resumed")
                        ),
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
