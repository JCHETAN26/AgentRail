"""Phase 10's exit criteria.

Two questions, and nothing else in this file:

* can a high-risk action reach the world without a human saying yes?
* can anything — a retry, a redelivery, a second worker, an event that arrives
  late — get past a human who said no?
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_run_runner import load_run, make_run

from agentrail_core.approvals import ApprovalRequest, ApprovalState
from agentrail_core.execution import EvaluationRunState, RunItem, RunItemState
from agentrail_core.ids import new_sortable_id
from agentrail_core.settings import DatabaseSettings
from agentrail_core.side_effects import SideEffectRecord
from agentrail_core.trajectories import Trajectory, TrajectoryStep
from agentrail_worker.run_runner import EvaluationRunRunner, RunOutcome

pytestmark = pytest.mark.integration

HIGH_RISK = {"tool_risks": {"restart_service": "HIGH_RISK_WRITE"}}
PROHIBITED = {"tool_risks": {"restart_service": "PROHIBITED"}}


async def effects(session_factory: async_sessionmaker[AsyncSession], run_id: str) -> int:
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(SideEffectRecord)
            .where(SideEffectRecord.run_id == run_id)
        )
    return int(count or 0)


async def approvals(
    session_factory: async_sessionmaker[AsyncSession], run_id: str
) -> list[ApprovalRequest]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(ApprovalRequest)
            .where(ApprovalRequest.run_id == run_id)
            .order_by(ApprovalRequest.created_at)
        )
        return list(rows.all())


async def items(session_factory: async_sessionmaker[AsyncSession], run_id: str) -> list[RunItem]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(RunItem).where(RunItem.run_id == run_id).order_by(RunItem.item_index)
        )
        return list(rows.all())


async def decide(
    session_factory: async_sessionmaker[AsyncSession],
    approval_id: str,
    *,
    approve: bool,
    edited: dict[str, object] | None = None,
) -> None:
    """Record a decision the way the API does, without the HTTP layer."""
    async with session_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None
        approval.state = ApprovalState.APPROVED if approve else ApprovalState.REJECTED
        approval.edited_arguments = edited
        await session.execute(
            update(RunItem)
            .where(RunItem.id == approval.run_item_id)
            .values(
                state=RunItemState.PENDING if approve else RunItemState.FAILED_TERMINAL,
                error_code=None if approve else "approval_rejected",
            )
        )
        await session.commit()


class TestApprovalIsRequired:
    async def test_a_high_risk_action_parks_without_reaching_the_world(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=1, policy_bundle=HIGH_RISK)
        runner = EvaluationRunRunner(session_factory, worker_id="policy-worker", lease_seconds=5)

        await runner.process(run_id)

        assert await effects(session_factory, run_id) == 0, "nothing may act before a human does"
        parked = (await items(session_factory, run_id))[0]
        assert parked.state == RunItemState.AWAITING_APPROVAL
        assert parked.lease_expires_at is None, "a reviewer is not on a worker's clock"
        pending = await approvals(session_factory, run_id)
        assert len(pending) == 1
        assert pending[0].state == ApprovalState.PENDING
        assert pending[0].tool == "restart_service"
        assert pending[0].risk_level == "HIGH_RISK_WRITE"

    async def test_the_parked_arguments_are_redacted_before_a_reviewer_sees_them(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=1, policy_bundle=HIGH_RISK)
        runner = EvaluationRunRunner(session_factory, worker_id="policy-worker", lease_seconds=5)
        await runner.process(run_id)

        request = (await approvals(session_factory, run_id))[0]

        assert request.requested_arguments["api_key"] == "[REDACTED]"

    async def test_waiting_for_a_human_does_not_consume_a_retry(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        """A slow reviewer must not exhaust the item's retry budget for it."""
        run_id = await make_run(
            session_factory, project_id, item_count=1, policy_bundle=HIGH_RISK, max_attempts=2
        )
        runner = EvaluationRunRunner(session_factory, worker_id="policy-worker", lease_seconds=5)

        await runner.process(run_id)

        assert (await items(session_factory, run_id))[0].attempt_count == 0

    async def test_a_run_with_a_parked_item_stays_open_rather_than_finishing(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=2, policy_bundle=HIGH_RISK)
        runner = EvaluationRunRunner(session_factory, worker_id="policy-worker", lease_seconds=5)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.SKIPPED
        assert (await load_run(session_factory, run_id)).state == EvaluationRunState.RUNNING

    async def test_a_prohibited_tool_is_denied_without_asking_anyone(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        """The point of having a level above 'needs approval'."""
        run_id = await make_run(session_factory, project_id, item_count=1, policy_bundle=PROHIBITED)
        runner = EvaluationRunRunner(session_factory, worker_id="policy-worker", lease_seconds=5)

        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.FAILED
        assert await effects(session_factory, run_id) == 0
        assert await approvals(session_factory, run_id) == []
        assert (await items(session_factory, run_id))[0].error_code == "policy_denied"

    async def test_an_unclassified_tool_stops_rather_than_sailing_through(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=1, policy_bundle={})
        runner = EvaluationRunRunner(session_factory, worker_id="policy-worker", lease_seconds=5)

        await runner.process(run_id)

        assert await effects(session_factory, run_id) == 0
        assert (await items(session_factory, run_id))[0].state == RunItemState.AWAITING_APPROVAL


class TestRejectionCannotBeBypassed:
    async def test_an_approved_action_runs_exactly_once_and_names_its_approval(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=1, policy_bundle=HIGH_RISK)
        runner = EvaluationRunRunner(session_factory, worker_id="policy-worker", lease_seconds=5)
        await runner.process(run_id)
        approval = (await approvals(session_factory, run_id))[0]

        await decide(session_factory, approval.id, approve=True)
        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.PASSED
        assert await effects(session_factory, run_id) == 1
        async with session_factory() as session:
            record = await session.scalar(
                select(SideEffectRecord).where(SideEffectRecord.run_id == run_id)
            )
        assert record is not None
        assert record.required_approval is True
        assert record.approval_id == approval.id

    async def test_a_rejected_action_never_reaches_the_world(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=1, policy_bundle=HIGH_RISK)
        runner = EvaluationRunRunner(session_factory, worker_id="policy-worker", lease_seconds=5)
        await runner.process(run_id)
        approval = (await approvals(session_factory, run_id))[0]

        await decide(session_factory, approval.id, approve=False)
        await runner.process(run_id)

        assert await effects(session_factory, run_id) == 0
        assert (await items(session_factory, run_id))[0].state == RunItemState.FAILED_TERMINAL

    async def test_a_delayed_delivery_after_a_rejection_still_cannot_execute_it(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        """The exit criterion, stated exactly.

        The item is forced back to PENDING after the rejection — as a stale
        in-flight message or a botched recovery sweep would leave it — and the
        run is reopened, so the worker genuinely re-executes it. The gate has to
        catch it on the approval's state alone.
        """
        run_id = await make_run(
            session_factory, project_id, item_count=1, policy_bundle=HIGH_RISK, max_attempts=5
        )
        runner = EvaluationRunRunner(session_factory, worker_id="policy-worker", lease_seconds=5)
        await runner.process(run_id)
        approval = (await approvals(session_factory, run_id))[0]
        await decide(session_factory, approval.id, approve=False)

        async with session_factory() as session:
            await session.execute(
                update(RunItem)
                .where(RunItem.run_id == run_id)
                .values(state=RunItemState.PENDING, completed_at=None, error_code=None)
            )
            await session.commit()

        await runner.process(run_id)

        assert await effects(session_factory, run_id) == 0, (
            "a late delivery must not execute what a reviewer rejected"
        )
        replayed = (await items(session_factory, run_id))[0]
        assert replayed.state == RunItemState.FAILED_TERMINAL
        assert replayed.error_code == "approval_rejected"
        assert (await approvals(session_factory, run_id))[0].state == ApprovalState.REJECTED

    async def test_a_reviewers_edit_is_what_actually_runs(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        run_id = await make_run(session_factory, project_id, item_count=1, policy_bundle=HIGH_RISK)
        runner = EvaluationRunRunner(session_factory, worker_id="policy-worker", lease_seconds=5)
        await runner.process(run_id)
        approval = (await approvals(session_factory, run_id))[0]

        await decide(
            session_factory,
            approval.id,
            approve=True,
            edited={"service": "service-0", "drain_first": True},
        )
        await runner.process(run_id)

        async with session_factory() as session:
            record = await session.scalar(
                select(SideEffectRecord).where(SideEffectRecord.run_id == run_id)
            )
        assert record is not None
        # The edit changes the arguments, so it changes the ledger key: a
        # different action is a different effect and gets its own row.
        assert record.idempotency_key != approval.idempotency_key
        assert record.approval_id == approval.id

    async def test_the_ledger_refuses_an_unapproved_high_risk_effect_directly(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        """The invariant does not depend on the gate being reached.

        Even a caller that skips policy entirely cannot write the row.
        """
        run_id = await make_run(session_factory, project_id, item_count=1, policy_bundle=HIGH_RISK)
        runner = EvaluationRunRunner(session_factory, worker_id="policy-worker", lease_seconds=5)
        await runner.process(run_id)
        item = (await items(session_factory, run_id))[0]

        async with session_factory() as session:
            session.add(
                SideEffectRecord(
                    id=new_sortable_id(),
                    project_id=project_id,
                    run_id=run_id,
                    run_item_id=item.id,
                    idempotency_key="f" * 64,
                    tool="restart_service",
                    arguments_digest="a" * 64,
                    applied_on_attempt=1,
                    result={"status": "ok"},
                    required_approval=True,
                    approval_id=None,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        assert await effects(session_factory, run_id) == 0

    async def test_two_workers_racing_a_parked_item_raise_one_request(
        self, session_factory: async_sessionmaker[AsyncSession], project_id: str
    ) -> None:
        """A reviewer sees the question once, not once per delivery."""
        run_id = await make_run(
            session_factory, project_id, item_count=1, policy_bundle=HIGH_RISK, max_attempts=5
        )
        first = EvaluationRunRunner(session_factory, worker_id="worker-a", lease_seconds=5)
        second = EvaluationRunRunner(session_factory, worker_id="worker-b", lease_seconds=5)

        await first.process(run_id)
        async with session_factory() as session:
            await session.execute(
                update(RunItem).where(RunItem.run_id == run_id).values(state=RunItemState.PENDING)
            )
            await session.commit()
        await second.process(run_id)

        assert len(await approvals(session_factory, run_id)) == 1
        assert await effects(session_factory, run_id) == 0


LANGGRAPH_MODEL = {"provider": "langgraph"}
LANGGRAPH_SPEC = {
    "nodes": [
        {"name": "act", "kind": "tool_call", "tool": "restart_service"},
        {"name": "gather", "kind": "evidence"},
        {"name": "decide", "kind": "decision"},
    ]
}


class TestApprovalUnderLangGraph:
    """Phase 11's exit criterion on the graph path: approve, resume, complete.

    The recorded path already proves the approval contract. What only a real
    database can show here is that the *interrupt* survives the process — the
    graph pauses at the node that asked, and the reviewer's decision resumes
    that node rather than replaying the graph.
    """

    async def test_a_high_risk_graph_node_parks_without_reaching_the_world(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        project_id: str,
        database_settings: DatabaseSettings,
    ) -> None:
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            policy_bundle=HIGH_RISK,
            model_config=LANGGRAPH_MODEL,
            graph_spec=LANGGRAPH_SPEC,
        )
        runner = EvaluationRunRunner(
            session_factory,
            worker_id="policy-worker",
            lease_seconds=5,
            database_url=str(database_settings.database_url),
        )

        await runner.process(run_id)

        assert await effects(session_factory, run_id) == 0
        assert (await items(session_factory, run_id))[0].state == RunItemState.AWAITING_APPROVAL
        assert len(await approvals(session_factory, run_id)) == 1

    async def test_approve_resume_complete(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        project_id: str,
        database_settings: DatabaseSettings,
    ) -> None:
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            policy_bundle=HIGH_RISK,
            model_config=LANGGRAPH_MODEL,
            graph_spec=LANGGRAPH_SPEC,
        )
        runner = EvaluationRunRunner(
            session_factory,
            worker_id="policy-worker",
            lease_seconds=5,
            database_url=str(database_settings.database_url),
        )
        await runner.process(run_id)
        approval = (await approvals(session_factory, run_id))[0]

        await decide(session_factory, approval.id, approve=True)
        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.PASSED
        item = (await items(session_factory, run_id))[0]
        assert item.state == RunItemState.COMPLETED
        assert item.result["mode"] == "langgraph"
        # Exactly once. A resume that replayed the graph would apply it twice,
        # and the ledger is what makes that visible.
        assert await effects(session_factory, run_id) == 1
        async with session_factory() as session:
            record = await session.scalar(
                select(SideEffectRecord).where(SideEffectRecord.run_id == run_id)
            )
        assert record is not None
        assert record.required_approval is True
        assert record.approval_id == approval.id

    async def test_resuming_does_not_re_run_the_node_before_the_interrupt(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        project_id: str,
        database_settings: DatabaseSettings,
    ) -> None:
        """The discriminator between resuming and replaying from START.

        With the high-risk tool as the graph's first node, a replay is
        indistinguishable from a resume: the ledger deduplicates the repeated
        effect under the same key, so exactly one record exists either way. A
        completed node *before* the interrupt is what makes the difference
        visible — on a replay it runs a second time.
        """
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            policy_bundle={
                "tool_risks": {
                    "search_logs": "READ_ONLY",
                    "restart_service": "HIGH_RISK_WRITE",
                }
            },
            model_config=LANGGRAPH_MODEL,
            graph_spec={
                "nodes": [
                    {"name": "probe", "kind": "tool_call", "tool": "search_logs"},
                    {"name": "act", "kind": "tool_call", "tool": "restart_service"},
                    {"name": "decide", "kind": "decision"},
                ]
            },
        )
        runner = EvaluationRunRunner(
            session_factory,
            worker_id="policy-worker",
            lease_seconds=5,
            database_url=str(database_settings.database_url),
        )
        await runner.process(run_id)
        approval = (await approvals(session_factory, run_id))[0]
        await decide(session_factory, approval.id, approve=True)
        await runner.process(run_id)

        async with session_factory() as session:
            item = (await items(session_factory, run_id))[0]
            steps = list(
                (
                    await session.scalars(
                        select(TrajectoryStep)
                        .join(Trajectory, Trajectory.id == TrajectoryStep.trajectory_id)
                        .where(Trajectory.run_item_id == item.id)
                    )
                ).all()
            )

        probes = [step for step in steps if step.title == "probe (tool_call)"]
        # Exactly one. Two would mean the graph restarted from START and ran an
        # already-completed node again.
        assert len(probes) == 1
        assert item.state == RunItemState.COMPLETED

    async def test_the_resumed_node_is_recorded_in_the_trajectory(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        project_id: str,
        database_settings: DatabaseSettings,
    ) -> None:
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            policy_bundle=HIGH_RISK,
            model_config=LANGGRAPH_MODEL,
            graph_spec=LANGGRAPH_SPEC,
        )
        runner = EvaluationRunRunner(
            session_factory,
            worker_id="policy-worker",
            lease_seconds=5,
            database_url=str(database_settings.database_url),
        )
        await runner.process(run_id)
        approval = (await approvals(session_factory, run_id))[0]
        await decide(session_factory, approval.id, approve=True)
        await runner.process(run_id)

        async with session_factory() as session:
            item = (await items(session_factory, run_id))[0]
            steps = list(
                (
                    await session.scalars(
                        select(TrajectoryStep)
                        .join(Trajectory, Trajectory.id == TrajectoryStep.trajectory_id)
                        .where(Trajectory.run_item_id == item.id)
                        .order_by(TrajectoryStep.step_index)
                    )
                ).all()
            )

        titles = [step.title for step in steps]
        # Losing the record of the effect a human authorised is the wrong thing
        # to lose; step indices on resume must not collide with the pre-approval
        # rows and silently drop it.
        assert "Awaiting approval" in titles
        assert "act (tool_call)" in titles
        assert len(steps) == len({step.step_index for step in steps})

    async def test_a_rejected_graph_node_never_reaches_the_world(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        project_id: str,
        database_settings: DatabaseSettings,
    ) -> None:
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            policy_bundle=HIGH_RISK,
            model_config=LANGGRAPH_MODEL,
            graph_spec=LANGGRAPH_SPEC,
        )
        runner = EvaluationRunRunner(
            session_factory,
            worker_id="policy-worker",
            lease_seconds=5,
            database_url=str(database_settings.database_url),
        )
        await runner.process(run_id)
        approval = (await approvals(session_factory, run_id))[0]

        await decide(session_factory, approval.id, approve=False)
        await runner.process(run_id)

        assert await effects(session_factory, run_id) == 0
        assert (await items(session_factory, run_id))[0].state == RunItemState.FAILED_TERMINAL


class TestEscalationChain:
    async def test_a_second_ask_on_the_same_item_escalates(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        project_id: str,
        database_settings: DatabaseSettings,
    ) -> None:
        """Reached the way production reaches it: two high-risk nodes.

        A parked item never retries while a human decides, so item attempt
        counts cannot drive this. What does is a graph whose *second* node
        raises a second approval request after the first was granted.
        """
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            policy_bundle={
                "tool_risks": {
                    "restart_service": "HIGH_RISK_WRITE",
                    "scale_service": "HIGH_RISK_WRITE",
                },
                "escalate_after_attempts": 1,
            },
            model_config=LANGGRAPH_MODEL,
            graph_spec={
                "nodes": [
                    {"name": "act", "kind": "tool_call", "tool": "restart_service"},
                    {"name": "grow", "kind": "tool_call", "tool": "scale_service"},
                ]
            },
        )
        runner = EvaluationRunRunner(
            session_factory,
            worker_id="policy-worker",
            lease_seconds=5,
            database_url=str(database_settings.database_url),
        )

        await runner.process(run_id)
        first = (await approvals(session_factory, run_id))[0]
        await decide(session_factory, first.id, approve=True)
        await runner.process(run_id)

        item = (await items(session_factory, run_id))[0]
        assert item.state == RunItemState.FAILED_TERMINAL
        # Distinct from a denial: scale_service was approvable, the chain simply
        # ran out of patience on this item.
        assert item.error_code == "approval_escalated"
        # The second node never asked, and never acted.
        assert len(await approvals(session_factory, run_id)) == 1
        assert await effects(session_factory, run_id) == 1

    async def test_an_already_approved_effect_is_never_escalated(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        project_id: str,
        database_settings: DatabaseSettings,
    ) -> None:
        # The converse failure: escalating an effect a human already approved
        # would block work that was explicitly authorised.
        run_id = await make_run(
            session_factory,
            project_id,
            item_count=1,
            policy_bundle={**HIGH_RISK, "escalate_after_attempts": 1},
            model_config=LANGGRAPH_MODEL,
            graph_spec=LANGGRAPH_SPEC,
        )
        runner = EvaluationRunRunner(
            session_factory,
            worker_id="policy-worker",
            lease_seconds=5,
            database_url=str(database_settings.database_url),
        )
        await runner.process(run_id)
        approval = (await approvals(session_factory, run_id))[0]

        await decide(session_factory, approval.id, approve=True)
        outcome = await runner.process(run_id)

        assert outcome is RunOutcome.PASSED
        assert (await items(session_factory, run_id))[0].state == RunItemState.COMPLETED
        assert await effects(session_factory, run_id) == 1
