"""Integration tests for durable evaluation-run execution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.approvals import ApprovalRequest, ApprovalState
from agentrail_core.datasets import Dataset, DatasetVersion, EvaluationSuite
from agentrail_core.evaluators import ComparisonReport
from agentrail_core.execution import EvaluationRun, EvaluationRunState, RunItem, RunItemState
from agentrail_core.identity import AgentDefinition, AgentVersion
from agentrail_core.ids import new_sortable_id
from agentrail_core.trajectories import Trajectory, TrajectoryStep, TrajectoryStepType
from agentrail_core.tribunal import TribunalSession
from agentrail_worker.run_runner import (
    EvaluationRunRunner,
    RunOutcome,
    _aggregate_run_state,
    _tribunal_gate_summary,
)

pytestmark = pytest.mark.integration


async def make_run(
    session_factory: async_sessionmaker[AsyncSession],
    project_id: str,
    *,
    item_count: int = 100,
    fault_profiles: list[dict[str, object]] | None = None,
    thresholds: dict[str, object] | None = None,
    max_attempts: int = 2,
    policy_bundle: dict[str, object] | None = None,
) -> str:
    agent = AgentDefinition(
        id=new_sortable_id(), project_id=project_id, name="Worker Agent", slug=new_sortable_id()
    )
    agent_version = AgentVersion(
        id=new_sortable_id(),
        agent_id=agent.id,
        version=1,
        content_digest="a" * 64,
        graph_spec={},
        prompt_bundle={},
        model_config={},
        tool_contracts=[],
        policy_bundle=policy_bundle
        if policy_bundle is not None
        else {"tool_risks": {"restart_service": "LOW_RISK_WRITE"}},
    )
    dataset = Dataset(
        id=new_sortable_id(), project_id=project_id, name="Data", slug=new_sortable_id()
    )
    dataset_version = DatasetVersion(
        id=new_sortable_id(),
        dataset_id=dataset.id,
        version=1,
        content_digest="b" * 64,
        storage_uri="s3://agentrail-datasets/test/data.jsonl",
        input_format="jsonl",
        record_schema={"required": ["id", "input", "expected"]},
        validation_report={"accepted_count": item_count, "rejected_count": 0},
        item_count=item_count,
        rejected_count=0,
        partition_counts={"default": item_count},
    )
    suite = EvaluationSuite(
        id=new_sortable_id(),
        project_id=project_id,
        dataset_version_id=dataset_version.id,
        name="Suite",
        slug=new_sortable_id(),
        evaluators=[],
        thresholds=thresholds or {},
        fault_profiles=fault_profiles or [],
        preview={"item_count": item_count},
        frozen_at=datetime.now(UTC),
    )
    run = EvaluationRun(
        id=new_sortable_id(),
        project_id=project_id,
        evaluation_suite_id=suite.id,
        candidate_agent_version_id=agent_version.id,
        execution_mode="recorded",
        state=EvaluationRunState.CREATED,
        correlation_id="cid_test",
        trace_id="b" * 32,
        item_count=item_count,
        summary={},
    )
    async with session_factory() as session:
        session.add_all([agent, agent_version, dataset, dataset_version, suite, run])
        for index in range(item_count):
            session.add(
                RunItem(
                    id=new_sortable_id(),
                    run_id=run.id,
                    item_index=index,
                    partition="default",
                    state=RunItemState.PENDING,
                    max_attempts=max_attempts,
                    checkpoint={"item_index": index},
                )
            )
        await session.commit()
    return run.id


async def load_run(session_factory: async_sessionmaker[AsyncSession], run_id: str) -> EvaluationRun:
    async with session_factory() as session:
        run = await session.get(EvaluationRun, run_id)
    assert run is not None
    return run


def test_tribunal_gate_forces_blocked_runs_to_failed() -> None:
    assert (
        _aggregate_run_state(failed_count=0, tribunal_outcome="blocked")
        is EvaluationRunState.FAILED
    )
    assert (
        _aggregate_run_state(failed_count=0, tribunal_outcome="conditional")
        is EvaluationRunState.PASSED
    )
    assert (
        _aggregate_run_state(failed_count=1, tribunal_outcome="conditional")
        is EvaluationRunState.PASSED
    )
    assert _aggregate_run_state(failed_count=1, tribunal_outcome=None) is EvaluationRunState.FAILED


def test_tribunal_gate_summary_marks_conditional_as_approval_required() -> None:
    assert _tribunal_gate_summary("blocked")["effect"] == "blocked_run"
    conditional = _tribunal_gate_summary("conditional")
    assert conditional["effect"] == "passed_with_warnings"
    assert conditional["requires_human_approval"] is True


class TestEvaluationRunRunner:
    async def test_completes_a_100_item_suite(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=100)
        runner = EvaluationRunRunner(session_factory, worker_id="run-worker", lease_seconds=5)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.PASSED
        run = await load_run(session_factory, run_id)
        assert run.state == EvaluationRunState.PASSED
        assert run.completed_count == 100
        assert run.failed_count == 0
        async with session_factory() as session:
            trajectory_count = await session.scalar(
                select(func.count()).select_from(Trajectory).where(Trajectory.run_id == run_id)
            )
            tool_step = await session.scalar(
                select(TrajectoryStep)
                .join(Trajectory, Trajectory.id == TrajectoryStep.trajectory_id)
                .where(
                    Trajectory.run_id == run_id,
                    TrajectoryStep.step_type == TrajectoryStepType.TOOL_CALL,
                )
                .order_by(TrajectoryStep.step_index)
                .limit(1)
            )
        assert trajectory_count == 100
        assert tool_step is not None
        assert tool_step.redacted_input["arguments"]["api_key"] == "[REDACTED]"

    async def test_auto_creates_tribunal_when_suite_enables_it(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=2,
            thresholds={"tribunal": {"enabled": True}},
        )
        runner = EvaluationRunRunner(session_factory, worker_id="run-worker", lease_seconds=5)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.PASSED
        async with session_factory() as session:
            run = await session.get(EvaluationRun, run_id)
            tribunal = await session.scalar(
                select(TribunalSession).where(TribunalSession.run_id == run_id)
            )
        assert run is not None
        assert tribunal is not None
        assert tribunal.outcome == "approved"
        assert run.summary["tribunal_session_id"] == tribunal.id
        assert run.summary["tribunal_outcome"] == "approved"
        assert run.summary["tribunal_gate"]["effect"] == "approved"

    async def test_tribunal_blocked_verdict_forces_run_failure(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        project_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=2,
            thresholds={"tribunal": {"enabled": True}},
        )
        runner = EvaluationRunRunner(session_factory, worker_id="run-worker", lease_seconds=5)

        async def non_reproducible_report(
            session: AsyncSession, *, run: EvaluationRun
        ) -> ComparisonReport:
            report = ComparisonReport(
                id=new_sortable_id(),
                project_id=run.project_id,
                run_id=run.id,
                baseline_agent_version_id=run.baseline_agent_version_id,
                candidate_agent_version_id=run.candidate_agent_version_id,
                suite_digest="blocked-by-tribunal",
                summary={
                    "item_count": run.item_count,
                    "result_count": run.item_count,
                    "pass_rate": 1.0,
                    "regression_count": 0,
                    "errors_in_denominator": True,
                    "reproducible": False,
                },
                evaluator_metrics={
                    "task_success": {
                        "total": run.item_count,
                        "passed": run.item_count,
                        "failed": 0,
                        "errors": 0,
                        "pass_rate": 1.0,
                        "mean_score": 1.0,
                    }
                },
                category_metrics={
                    "quality": {
                        "total": run.item_count,
                        "passed": run.item_count,
                        "failed": 0,
                        "errors": 0,
                        "pass_rate": 1.0,
                        "mean_score": 1.0,
                    }
                },
                regressions=[],
                exports={},
            )
            session.add(report)
            await session.flush()
            return report

        monkeypatch.setattr(runner, "_build_comparison_report", non_reproducible_report)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.FAILED
        async with session_factory() as session:
            run = await session.get(EvaluationRun, run_id)
            tribunal = await session.scalar(
                select(TribunalSession).where(TribunalSession.run_id == run_id)
            )
        assert run is not None
        assert tribunal is not None
        assert tribunal.outcome == "blocked"
        assert run.state == EvaluationRunState.FAILED
        assert run.failed_count == 0
        assert run.summary["tribunal_outcome"] == "blocked"
        assert run.summary["tribunal_gate"]["effect"] == "blocked_run"

    async def test_conditional_tribunal_verdict_passes_with_human_approval_warning(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        project_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=2,
            thresholds={"tribunal": {"enabled": True}},
        )
        runner = EvaluationRunRunner(session_factory, worker_id="run-worker", lease_seconds=5)

        async def warning_report(session: AsyncSession, *, run: EvaluationRun) -> ComparisonReport:
            report = ComparisonReport(
                id=new_sortable_id(),
                project_id=run.project_id,
                run_id=run.id,
                baseline_agent_version_id=run.baseline_agent_version_id,
                candidate_agent_version_id=run.candidate_agent_version_id,
                suite_digest="conditional-tribunal",
                summary={
                    "item_count": run.item_count,
                    "result_count": run.item_count,
                    "pass_rate": 0.95,
                    "regression_count": 1,
                    "errors_in_denominator": True,
                    "reproducible": True,
                },
                evaluator_metrics={
                    "task_success": {
                        "total": run.item_count,
                        "passed": run.item_count,
                        "failed": 0,
                        "errors": 0,
                        "pass_rate": 1.0,
                        "mean_score": 1.0,
                    }
                },
                category_metrics={
                    "quality": {
                        "total": run.item_count,
                        "passed": run.item_count,
                        "failed": 0,
                        "errors": 0,
                        "pass_rate": 1.0,
                        "mean_score": 1.0,
                    }
                },
                regressions=[
                    {
                        "run_item_id": "recorded-warning",
                        "item_index": 0,
                        "partition": "default",
                        "evaluator_slug": "task_success",
                        "category": "quality",
                        "state": "FAILED",
                        "score": 0.95,
                    }
                ],
                exports={},
            )
            session.add(report)
            await session.flush()
            return report

        monkeypatch.setattr(runner, "_build_comparison_report", warning_report)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.PASSED
        async with session_factory() as session:
            run = await session.get(EvaluationRun, run_id)
            tribunal = await session.scalar(
                select(TribunalSession).where(TribunalSession.run_id == run_id)
            )
            approval = await session.scalar(
                select(ApprovalRequest).where(ApprovalRequest.run_id == run_id)
            )
        assert run is not None
        assert tribunal is not None
        assert approval is not None
        assert tribunal.outcome == "conditional"
        assert run.state == EvaluationRunState.PASSED
        assert str(approval.state) == ApprovalState.PENDING.value
        assert approval.tool == "tribunal_conditional_release"
        assert run.summary["tribunal_conditional_approval_id"] == approval.id
        assert run.summary["tribunal_conditional_approval_state"] == "PENDING"
        assert run.summary["tribunal_gate"]["effect"] == "passed_with_warnings"
        assert run.summary["tribunal_gate"]["requires_human_approval"] is True

    async def test_does_not_create_tribunal_by_default(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=2)
        runner = EvaluationRunRunner(session_factory, worker_id="run-worker", lease_seconds=5)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.PASSED
        async with session_factory() as session:
            tribunal_count = await session.scalar(
                select(func.count())
                .select_from(TribunalSession)
                .where(TribunalSession.run_id == run_id)
            )
        assert tribunal_count == 0

    async def test_duplicate_run_delivery_is_harmless(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=2)
        runner = EvaluationRunRunner(session_factory, worker_id="run-worker", lease_seconds=5)
        await runner.process(run_id)

        duplicate = await runner.process(run_id)

        assert duplicate is RunOutcome.SKIPPED
        assert (await load_run(session_factory, run_id)).completed_count == 2

    async def test_resumes_a_running_run_after_recovery_requeues_it(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=2)
        async with session_factory() as session:
            run = await session.get(EvaluationRun, run_id)
            assert run is not None
            run.state = EvaluationRunState.RUNNING
            await session.commit()
        runner = EvaluationRunRunner(session_factory, worker_id="run-worker", lease_seconds=5)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.PASSED
        assert (await load_run(session_factory, run_id)).completed_count == 2

    async def test_expired_leases_return_to_pending_with_budget(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=1)
        async with session_factory() as session:
            item = await session.scalar(select(RunItem).where(RunItem.run_id == run_id))
            assert item is not None
            item.state = RunItemState.EXECUTING
            item.attempt_count = 1
            item.max_attempts = 2
            item.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        recovered = await EvaluationRunRunner.recover_expired_leases(
            session_factory, now=datetime.now(UTC)
        )

        async with session_factory() as session:
            item = await session.scalar(select(RunItem).where(RunItem.run_id == run_id))
        assert recovered == 1
        assert item is not None
        assert item.state == RunItemState.PENDING

    async def test_finds_stale_nonterminal_runs_for_requeue(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=1)
        stale_before = datetime.now(UTC) - timedelta(seconds=60)
        async with session_factory() as session:
            run = await session.get(EvaluationRun, run_id)
            assert run is not None
            run.state = EvaluationRunState.RUNNING
            run.updated_at = stale_before - timedelta(seconds=1)
            await session.commit()

        recovered = await EvaluationRunRunner.recoverable_run_ids(
            session_factory, before=stale_before, limit=10
        )

        assert recovered == [run_id]
