"""LangGraph graph-building, capture and validation tests.

The graph topology, the node semantics and the event capture hook are all
exercised here against an in-memory checkpointer, so they run in the default
unit suite with no PostgreSQL. The durable-checkpoint path is covered by
``test_langgraph_checkpointer.py``, which is marked ``integration``.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentrail_worker.langgraph_executor import (
    DEFAULT_NODE,
    AgentState,
    CapturedEvent,
    ExecutionOutcome,
    GraphSpecError,
    ToolInvocation,
    ToolResult,
    build_graph,
    checkpointer_conn_string,
    parse_graph_spec,
)


class RecordingGateway:
    """A gateway that records what the graph asked for."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.calls: list[ToolInvocation] = []
        self._fail_with = fail_with

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls.append(invocation)
        if self._fail_with is not None:
            raise self._fail_with
        return ToolResult(
            tool=invocation.tool, output={"status": "ok", "restarted": True}, latency_ms=7
        )


async def run_graph(spec: dict[str, object], gateway: RecordingGateway) -> ExecutionOutcome:
    """Compile and stream a graph the way LangGraphExecutor does, in memory."""
    nodes = parse_graph_spec(spec)
    compiled = build_graph(nodes, gateway).compile(checkpointer=InMemorySaver())
    kinds = {node.name: node.kind for node in nodes}
    initial: AgentState = {
        "item_index": 3,
        "partition": "p1",
        "tool_results": [],
        "evidence": [],
        "passed": False,
    }
    outcome = ExecutionOutcome(thread_id="thread-1")
    merged: dict[str, object] = dict(initial)
    async for update in compiled.astream(
        initial, config={"configurable": {"thread_id": "thread-1"}}, stream_mode="updates"
    ):
        for node_name, node_state in update.items():
            if isinstance(node_state, dict):
                merged.update(node_state)
            outcome.events.append(
                CapturedEvent(
                    node=node_name, kind=kinds.get(node_name, "decision"), state=dict(merged)
                )
            )
    outcome.final_state = merged
    outcome.passed = bool(merged.get("passed", False))
    return outcome


class TestGraphSpecParsing:
    def test_absent_nodes_yield_one_decision_node(self) -> None:
        # Agent versions predating graph-spec nodes must stay executable.
        (node,) = parse_graph_spec({"entrypoint": "run"})

        assert node.name == DEFAULT_NODE
        assert node.kind == "decision"
        assert node.tool is None

    def test_nodes_are_parsed_in_order(self) -> None:
        nodes = parse_graph_spec(
            {
                "nodes": [
                    {"name": "act", "kind": "tool_call", "tool": "restart_service"},
                    {"name": "gather", "kind": "evidence"},
                    {"name": "decide", "kind": "decision"},
                ]
            }
        )

        assert [node.name for node in nodes] == ["act", "gather", "decide"]
        assert nodes[0].tool == "restart_service"

    @pytest.mark.parametrize(
        ("spec", "message"),
        [
            ("not-an-object", "graph_spec must be an object"),
            ({"nodes": []}, "non-empty list"),
            ({"nodes": "no"}, "non-empty list"),
            ({"nodes": [{"name": "a", "kind": "sudo"}]}, "kind must be one of"),
            ({"nodes": [{"name": "a", "kind": "tool_call"}]}, "needs a tool name"),
            ({"nodes": [{"name": "a", "kind": "evidence", "tool": "x"}]}, "must not name a tool"),
            ({"nodes": [{"name": "a"}, {"name": "a"}]}, "duplicate node name"),
            ({"nodes": [{"name": "a", "arguments": []}]}, "arguments must be an object"),
            # Unhashable kinds would raise TypeError from the set membership
            # test rather than the GraphSpecError this promises.
            ({"nodes": [{"name": "a", "kind": []}]}, "kind must be one of"),
            ({"nodes": [{"name": "a", "kind": {}}]}, "kind must be one of"),
            # LangGraph's own sentinels are valid JSON but illegal node names.
            ({"nodes": [{"name": "__start__"}]}, "reserved by LangGraph"),
            ({"nodes": [{"name": "__end__"}]}, "reserved by LangGraph"),
        ],
    )
    def test_unusable_specs_are_rejected(self, spec: object, message: str) -> None:
        # Graph specs are tenant-supplied, so anything unrecognised is refused
        # rather than coerced into something that runs differently than written.
        with pytest.raises(GraphSpecError, match=message):
            parse_graph_spec(spec)


class TestGraphExecution:
    async def test_tool_nodes_go_through_the_gateway(self) -> None:
        gateway = RecordingGateway()

        outcome = await run_graph(
            {
                "nodes": [
                    {
                        "name": "act",
                        "kind": "tool_call",
                        "tool": "restart_service",
                        "arguments": {"service": "checkout"},
                    },
                    {"name": "decide", "kind": "decision"},
                ]
            },
            gateway,
        )

        # The graph never touches a tool directly; the runner's gateway is the
        # only door, which is what keeps policy and budgets enforceable.
        assert [call.tool for call in gateway.calls] == ["restart_service"]
        assert gateway.calls[0].arguments == {"service": "checkout"}
        assert outcome.final_state["tool_results"] == [
            {
                "tool": "restart_service",
                "output": {"status": "ok", "restarted": True},
                "latency_ms": 7,
            }
        ]
        assert outcome.passed is True

    async def test_every_node_transition_is_captured_with_its_state(self) -> None:
        gateway = RecordingGateway()

        outcome = await run_graph(
            {
                "nodes": [
                    {"name": "act", "kind": "tool_call", "tool": "restart_service"},
                    {"name": "gather", "kind": "evidence"},
                    {"name": "decide", "kind": "decision"},
                ]
            },
            gateway,
        )

        # One event per node, in order, each carrying the state as of that node.
        assert [event.node for event in outcome.events] == ["act", "gather", "decide"]
        assert [event.kind for event in outcome.events] == ["tool_call", "evidence", "decision"]
        assert outcome.events[0].state["tool_results"] != []
        # Evidence has not been gathered yet at the tool_call node, which is the
        # whole point of capturing per-step state rather than one final snapshot.
        assert outcome.events[0].state["evidence"] == []
        assert outcome.events[1].state["evidence"] == [
            {"kind": "langgraph", "node": "gather", "supports": "passed"}
        ]
        assert outcome.events[2].state["passed"] is True

    async def test_captured_states_are_independent_snapshots(self) -> None:
        gateway = RecordingGateway()

        outcome = await run_graph(
            {"nodes": [{"name": "one", "kind": "evidence"}, {"name": "two", "kind": "evidence"}]},
            gateway,
        )

        # A shared mutable dict would make every step show the final state.
        assert len(outcome.events[0].state["evidence"]) == 1
        assert len(outcome.events[1].state["evidence"]) == 2

    async def test_a_failing_tool_call_propagates(self) -> None:
        gateway = RecordingGateway(fail_with=RuntimeError("policy denied"))

        # The runner decides what a denial means for the item; the executor must
        # not swallow it and report a passing graph.
        with pytest.raises(RuntimeError, match="policy denied"):
            await run_graph(
                {"nodes": [{"name": "act", "kind": "tool_call", "tool": "restart_service"}]},
                gateway,
            )


class TestCheckpointerConnString:
    def test_sqlalchemy_driver_suffix_is_stripped(self) -> None:
        # psycopg rejects the "+psycopg" that SQLAlchemy requires.
        assert (
            checkpointer_conn_string("postgresql+psycopg://u:p@localhost:5433/agentrail")
            == "postgresql://u:p@localhost:5433/agentrail"
        )

    def test_a_plain_url_is_unchanged(self) -> None:
        assert (
            checkpointer_conn_string("postgresql://u:p@localhost:5433/db")
            == "postgresql://u:p@localhost:5433/db"
        )


class TestResumeSemantics:
    async def test_a_completed_thread_is_not_re_run(self) -> None:
        """The bug this guards: re-submitting input restarts the graph.

        LangGraph treats any non-None input on an existing thread as a fresh
        invocation from START. A retried item reuses its thread id, so passing
        the initial state again would call every tool node a second time and
        charge its budget again.
        """
        gateway = RecordingGateway()
        nodes = parse_graph_spec(
            {"nodes": [{"name": "act", "kind": "tool_call", "tool": "restart_service"}]}
        )
        saver = InMemorySaver()
        compiled = build_graph(nodes, gateway).compile(checkpointer=saver)
        config = {"configurable": {"thread_id": "retry-me"}}
        await compiled.ainvoke(
            {"item_index": 1, "partition": "p0", "tool_results": [], "evidence": []},
            config=config,
        )
        assert len(gateway.calls) == 1

        # What execute() now does on a thread that already has checkpoints.
        snapshot = await compiled.aget_state(config)
        assert snapshot.created_at is not None
        await compiled.ainvoke(None, config=config)

        assert len(gateway.calls) == 1

    async def test_resubmitting_input_would_repeat_the_tool_call(self) -> None:
        # The counter-example, asserted so the reason for the None is explicit
        # rather than folklore: this is what the old code did.
        gateway = RecordingGateway()
        nodes = parse_graph_spec(
            {"nodes": [{"name": "act", "kind": "tool_call", "tool": "restart_service"}]}
        )
        compiled = build_graph(nodes, gateway).compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "retry-me"}}
        initial = {"item_index": 1, "partition": "p0", "tool_results": [], "evidence": []}

        await compiled.ainvoke(initial, config=config)
        await compiled.ainvoke(initial, config=config)

        assert len(gateway.calls) == 2


class TestEventSink:
    async def test_the_sink_receives_events_before_a_failure(self) -> None:
        """A halt unwinds by exception, so the sink is the only way to keep the trace."""
        calls = {"n": 0}

        class FailsOnSecond:
            async def invoke(self, invocation: ToolInvocation) -> ToolResult:
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("policy denied")
                return ToolResult(tool=invocation.tool, output={"status": "ok"})

        nodes = parse_graph_spec(
            {
                "nodes": [
                    {"name": "first", "kind": "tool_call", "tool": "restart_service"},
                    {"name": "second", "kind": "tool_call", "tool": "scale_service"},
                ]
            }
        )
        compiled = build_graph(nodes, FailsOnSecond()).compile(checkpointer=InMemorySaver())
        sink: list[CapturedEvent] = []
        kinds = {node.name: node.kind for node in nodes}

        with pytest.raises(RuntimeError, match="policy denied"):
            async for update in compiled.astream(
                {"item_index": 1, "partition": "p0", "tool_results": [], "evidence": []},
                config={"configurable": {"thread_id": "halt"}},
                stream_mode="updates",
            ):
                for node_name, node_state in update.items():
                    sink.append(
                        CapturedEvent(
                            node=node_name,
                            kind=kinds.get(node_name, "decision"),
                            state=dict(node_state) if isinstance(node_state, dict) else {},
                        )
                    )

        # The first node's transition survives the second node's failure.
        assert [event.node for event in sink] == ["first"]
