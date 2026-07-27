"""Phase 9's exit criterion, under forced failure.

Every test here exists to answer one question: after a fault, a retry, a lease
expiry or a duplicate delivery, did the effect reach the world exactly once?
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_run_runner import load_run, make_run

from agentrail_core.execution import (
    EvaluationRun,
    EvaluationRunState,
    RunItem,
    RunItemState,
)
from agentrail_core.ids import new_sortable_id
from agentrail_core.side_effects import SideEffectRecord, side_effect_key
from agentrail_worker.run_runner import EvaluationRunRunner, RunOutcome

pytestmark = pytest.mark.integration


async def side_effect_count(session_factory: async_sessionmaker[AsyncSession], run_id: str) -> int:
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(SideEffectRecord)
            .where(SideEffectRecord.run_id == run_id)
        )
    return int(count or 0)


async def load_items(
    session_factory: async_sessionmaker[AsyncSession], run_id: str
) -> list[RunItem]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(RunItem).where(RunItem.run_id == run_id).order_by(RunItem.item_index)
        )
        return list(rows.all())


class TestZeroDuplicateSideEffects:
    async def test_a_retried_item_does_not_repeat_its_side_effect(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        """The central case. Attempt 1 applies the effect and is then killed by
        an injected timeout; attempt 2 must find the ledger row and decline."""
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            fault_profiles=[{"kind": "tool.timeout", "attempts": [1]}],
        )
        runner = EvaluationRunRunner(session_factory, worker_id="fault-worker", lease_seconds=5)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.PASSED
        items = await load_items(session_factory, run_id)
        assert items[0].state == RunItemState.COMPLETED
        assert items[0].attempt_count == 2, "the first attempt should have been retried"
        assert await side_effect_count(session_factory, run_id) == 1

        async with session_factory() as session:
            record = await session.scalar(
                select(SideEffectRecord).where(SideEffectRecord.run_id == run_id)
            )
        assert record is not None
        assert record.applied_on_attempt == 1, "the effect belongs to the attempt that applied it"

    async def test_a_reasoning_failure_goes_terminal_without_burning_a_retry(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        """A refusal reproduces identically on a second attempt, so retrying it
        spends the budget and hides the finding."""
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            fault_profiles=[{"kind": "model.refusal"}],
        )
        runner = EvaluationRunRunner(session_factory, worker_id="fault-worker", lease_seconds=5)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.FAILED
        items = await load_items(session_factory, run_id)
        assert items[0].state == RunItemState.FAILED_TERMINAL
        assert items[0].attempt_count == 1
        assert items[0].error_code == "model.refusal"
        assert items[0].injected_fault is not None
        assert items[0].injected_fault["retryable"] is False
        assert await side_effect_count(session_factory, run_id) == 1

    async def test_a_retryable_fault_that_never_clears_stops_at_max_attempts(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            fault_profiles=[{"kind": "tool.http_500"}],
            max_attempts=3,
        )
        runner = EvaluationRunRunner(session_factory, worker_id="fault-worker", lease_seconds=5)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.FAILED
        items = await load_items(session_factory, run_id)
        assert items[0].state == RunItemState.FAILED_TERMINAL
        assert items[0].attempt_count == 3
        assert await side_effect_count(session_factory, run_id) == 1, (
            "three attempts, one effect — the retries must not re-apply it"
        )

    async def test_duplicate_run_delivery_cannot_double_apply_an_effect(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=3)
        runner = EvaluationRunRunner(session_factory, worker_id="fault-worker", lease_seconds=5)

        await runner.process(run_id)
        await runner.process(run_id)
        await runner.process(run_id)

        assert await side_effect_count(session_factory, run_id) == 3

    async def test_a_second_worker_racing_the_same_run_applies_nothing_twice(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        """At-least-once delivery means two workers can hold the same run."""
        run_id = await make_run(session_factory, project_id, item_count=5)
        first = EvaluationRunRunner(session_factory, worker_id="worker-a", lease_seconds=5)
        second = EvaluationRunRunner(session_factory, worker_id="worker-b", lease_seconds=5)

        await first.process(run_id)
        await second.process(run_id)

        assert await side_effect_count(session_factory, run_id) == 5

    async def test_lease_expiry_after_a_partial_attempt_does_not_duplicate_the_effect(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        """Worker termination, modelled exactly as the platform sees it: an item
        left leased by a worker that never came back."""
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            fault_profiles=[{"kind": "tool.timeout", "attempts": [1]}],
            max_attempts=5,
        )
        runner = EvaluationRunRunner(session_factory, worker_id="doomed-worker", lease_seconds=5)
        await runner.process(run_id)
        assert await side_effect_count(session_factory, run_id) == 1

        # Strand the item in a leased state with a dead lease, as though the
        # worker had died holding it. The run goes back to RUNNING with it: a
        # worker dying mid-run leaves the run open, and a run that has already
        # reached a terminal state is deliberately never re-processed.
        async with session_factory() as session:
            await session.execute(
                update(RunItem)
                .where(RunItem.run_id == run_id)
                .values(
                    state=RunItemState.LEASED,
                    attempt_count=1,
                    lease_expires_at=datetime.now(UTC) - timedelta(minutes=5),
                    completed_at=None,
                )
            )
            await session.execute(
                update(EvaluationRun)
                .where(EvaluationRun.id == run_id)
                .values(
                    state=EvaluationRunState.RUNNING,
                    completed_count=0,
                    failed_count=0,
                    completed_at=None,
                )
            )
            await session.commit()

        recovered = await EvaluationRunRunner.recover_expired_leases(
            session_factory, now=datetime.now(UTC)
        )
        assert recovered == 1

        await runner.process(run_id)

        assert await side_effect_count(session_factory, run_id) == 1
        assert (await load_items(session_factory, run_id))[0].state == RunItemState.COMPLETED

    async def test_budget_exhaustion_is_terminal_and_names_the_budget(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            thresholds={"budgets": {"tool_calls": 0}},
        )
        runner = EvaluationRunRunner(session_factory, worker_id="fault-worker", lease_seconds=5)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.FAILED
        items = await load_items(session_factory, run_id)
        assert items[0].state == RunItemState.FAILED_TERMINAL
        assert items[0].error_code == "budget_exhausted"
        assert items[0].budget_state["spent"]["tool_calls"] == 1
        assert items[0].budget_state["limits"]["tool_calls"] == 0
        assert await side_effect_count(session_factory, run_id) == 0, (
            "the budget must stop the effect before it reaches the world"
        )

    async def test_the_ledger_constraint_refuses_a_duplicate_key_directly(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        """The invariant does not depend on the executor being careful.

        Even a caller that skips every check cannot write the second row.
        """
        run_id = await make_run(session_factory, project_id, item_count=1)
        runner = EvaluationRunRunner(session_factory, worker_id="fault-worker", lease_seconds=5)
        await runner.process(run_id)
        item = (await load_items(session_factory, run_id))[0]
        key = side_effect_key(
            run_item_id=item.id,
            step_index=2,
            tool="restart_service",
            arguments={"service": "service-0", "api_key": "test-secret-key"},
        )

        async with session_factory() as session:
            session.add(
                SideEffectRecord(
                    id=new_sortable_id(),
                    project_id=project_id,
                    run_id=run_id,
                    run_item_id=item.id,
                    idempotency_key=key,
                    tool="restart_service",
                    arguments_digest="c" * 64,
                    applied_on_attempt=2,
                    result={"status": "ok"},
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        assert await side_effect_count(session_factory, run_id) == 1

    async def test_a_faulted_run_still_reaches_a_terminal_run_state(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        """Correct state under forced failure — the other half of the criterion."""
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=4,
            fault_profiles=[{"kind": "model.invalid_arguments", "every_n": 2}],
        )
        runner = EvaluationRunRunner(session_factory, worker_id="fault-worker", lease_seconds=5)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.FAILED
        run = await load_run(session_factory, run_id)
        assert run.state == EvaluationRunState.FAILED
        assert run.failed_count == 2, "items 0 and 2 carry the fault"
        assert run.completed_count == 2
        items = await load_items(session_factory, run_id)
        assert [item.state for item in items] == [
            RunItemState.FAILED_TERMINAL,
            RunItemState.COMPLETED,
            RunItemState.FAILED_TERMINAL,
            RunItemState.COMPLETED,
        ]
        assert await side_effect_count(session_factory, run_id) == 4
