"""Integration tests for durable evaluation-run execution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.datasets import Dataset, DatasetVersion, EvaluationSuite
from agentrail_core.execution import EvaluationRun, EvaluationRunState, RunItem, RunItemState
from agentrail_core.identity import AgentDefinition, AgentVersion
from agentrail_core.ids import new_sortable_id
from agentrail_worker.run_runner import EvaluationRunRunner, RunOutcome

pytestmark = pytest.mark.integration


async def make_run(
    session_factory: async_sessionmaker[AsyncSession], project_id: str, *, item_count: int = 100
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
        policy_bundle={},
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
        thresholds={},
        fault_profiles=[],
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
