"""LangGraph durable-checkpoint tests against real PostgreSQL.

The unit tests cover graph topology and the capture hook with an in-memory
saver. What can only be proved against a real database is the property Phase 5
actually asks for: that a graph's state survives the process that produced it,
so a killed worker resumes from LangGraph's last committed checkpoint rather
than re-running the item from the start.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from agentrail_core.ids import new_sortable_id
from agentrail_core.settings import DatabaseSettings
from agentrail_worker.langgraph_executor import (
    ToolInvocation,
    ToolResult,
    build_graph,
    checkpointer_conn_string,
    parse_graph_spec,
)

pytestmark = pytest.mark.integration


class CountingGateway:
    """Counts tool invocations so re-execution is detectable."""

    def __init__(self) -> None:
        self.calls: list[ToolInvocation] = []

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls.append(invocation)
        return ToolResult(tool=invocation.tool, output={"status": "ok"}, latency_ms=1)


SPEC = {
    "nodes": [
        {"name": "act", "kind": "tool_call", "tool": "restart_service"},
        {"name": "gather", "kind": "evidence"},
        {"name": "decide", "kind": "decision"},
    ]
}


@pytest.fixture
def conn_string(database_settings: DatabaseSettings, migrated_database: str) -> str:
    """A psycopg-compatible URL for the same database the platform uses.

    Depends on ``migrated_database`` only to inherit its skip/fail behaviour when
    PostgreSQL is absent; LangGraph creates its own tables via ``setup()``.
    """
    return checkpointer_conn_string(str(database_settings.database_url))


class TestDurableCheckpoints:
    async def test_graph_state_survives_a_new_saver(self, conn_string: str) -> None:
        thread_id = f"item-{new_sortable_id()}"
        config = {"configurable": {"thread_id": thread_id}}
        nodes = parse_graph_spec(SPEC)

        async with AsyncPostgresSaver.from_conn_string(conn_string) as saver:
            await saver.setup()
            compiled = build_graph(nodes, CountingGateway()).compile(checkpointer=saver)
            await compiled.ainvoke(
                {"item_index": 4, "partition": "p0", "tool_results": [], "evidence": []},
                config=config,
            )

        # A completely separate saver, as a restarted worker would build.
        async with AsyncPostgresSaver.from_conn_string(conn_string) as reopened:
            compiled = build_graph(nodes, CountingGateway()).compile(checkpointer=reopened)
            state = await compiled.aget_state(config)

        assert state is not None
        assert state.values["passed"] is True
        assert state.values["item_index"] == 4
        assert len(state.values["tool_results"]) == 1

    async def test_resuming_a_finished_thread_does_not_repeat_side_effects(
        self, conn_string: str
    ) -> None:
        thread_id = f"item-{new_sortable_id()}"
        config = {"configurable": {"thread_id": thread_id}}
        nodes = parse_graph_spec(SPEC)
        first = CountingGateway()

        async with AsyncPostgresSaver.from_conn_string(conn_string) as saver:
            await saver.setup()
            compiled = build_graph(nodes, first).compile(checkpointer=saver)
            await compiled.ainvoke(
                {"item_index": 5, "partition": "p0", "tool_results": [], "evidence": []},
                config=config,
            )

        second = CountingGateway()
        async with AsyncPostgresSaver.from_conn_string(conn_string) as reopened:
            compiled = build_graph(nodes, second).compile(checkpointer=reopened)
            # Resuming a completed thread must not re-run its nodes. If it did,
            # a worker that died after committing would restart the tool call —
            # which is exactly the duplicate effect the ledger exists to prevent.
            await compiled.ainvoke(None, config=config)

        assert len(first.calls) == 1
        assert second.calls == []

    async def test_threads_are_isolated_from_each_other(self, conn_string: str) -> None:
        nodes = parse_graph_spec(SPEC)
        one = {"configurable": {"thread_id": f"item-{new_sortable_id()}"}}
        two = {"configurable": {"thread_id": f"item-{new_sortable_id()}"}}

        async with AsyncPostgresSaver.from_conn_string(conn_string) as saver:
            await saver.setup()
            compiled = build_graph(nodes, CountingGateway()).compile(checkpointer=saver)
            await compiled.ainvoke(
                {"item_index": 1, "partition": "p0", "tool_results": [], "evidence": []},
                config=one,
            )
            await compiled.ainvoke(
                {"item_index": 2, "partition": "p1", "tool_results": [], "evidence": []},
                config=two,
            )
            first = await compiled.aget_state(one)
            second = await compiled.aget_state(two)

        # Thread ids derive from the run item, so two items in the same run must
        # never read each other's graph state.
        assert first.values["item_index"] == 1
        assert first.values["partition"] == "p0"
        assert second.values["item_index"] == 2
        assert second.values["partition"] == "p1"
