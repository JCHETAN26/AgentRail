"""LangGraph-backed agent execution.

This module is the *only* place in AgentRail that imports LangGraph. The domain
package (``agentrail_core``) stays framework-free so that evaluation, policy and
tribunal logic never depend on an agent runtime — see
``docs/adr/0007-langgraph-as-an-execution-adapter.md``.

Two things make a LangGraph run observable to the platform:

* **Durable checkpoints.** The graph is compiled with LangGraph's own
  ``AsyncPostgresSaver``, keyed by a thread id derived from the run item, so a
  killed worker resumes from LangGraph's last committed checkpoint rather than
  restarting the item.
* **An event capture hook.** ``astream`` is consumed in ``updates`` mode, so
  every node transition becomes one captured event carrying the state that node
  produced. The runner turns those into ``TrajectoryStep`` rows, which is what
  makes the trace explorer's per-step graph state real for LangGraph runs.

Nothing here writes to the database or decides policy. Tool calls are delegated
to a gateway supplied by the runner, because the budget ledger, the policy gate
and the idempotent side-effect ledger are platform guarantees that must hold
whichever framework is executing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Protocol, TypedDict

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from agentrail_core.logging import get_logger

logger = get_logger(__name__)

#: Node name used when a graph spec does not name its own nodes.
DEFAULT_NODE = "agent"

#: Graph specs may not nominate arbitrary Python; only these node kinds exist.
_SUPPORTED_NODE_KINDS = frozenset({"tool_call", "evidence", "decision"})


class GraphSpecError(ValueError):
    """The agent version's ``graph_spec`` cannot be compiled into a graph."""


class AgentState(TypedDict, total=False):
    """State threaded through the graph and checkpointed at every node.

    Deliberately plain JSON-compatible values: this is persisted by LangGraph's
    checkpointer and surfaced in the trace explorer, so it has to survive a
    round trip through the database and into the browser.
    """

    item_index: int
    partition: str
    tool_results: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    passed: bool
    halted_reason: str


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """A tool the graph wants to call."""

    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The outcome of a tool call, as the platform applied it."""

    tool: str
    output: dict[str, Any]
    latency_ms: int = 0


class ToolGateway(Protocol):
    """The runner's controlled door to the outside world.

    A graph never touches a tool directly. The implementation the runner passes
    in charges the budget ledger, runs the policy gate, and applies the effect
    through the idempotent side-effect ledger, so a graph cannot bypass any of
    those by construction.
    """

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        """Apply a tool call, or raise if policy or budget forbids it."""


@dataclass(frozen=True, slots=True)
class CapturedEvent:
    """One node transition, ready to become a ``TrajectoryStep``."""

    node: str
    kind: str
    state: dict[str, Any]


@dataclass(slots=True)
class ExecutionOutcome:
    """Everything the runner needs to record after a graph finishes."""

    events: list[CapturedEvent] = field(default_factory=list)
    final_state: dict[str, Any] = field(default_factory=dict)
    passed: bool = False
    thread_id: str = ""


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One validated node from a graph spec."""

    name: str
    kind: str
    tool: str | None
    arguments: dict[str, Any]


#: LangGraph parameterises StateGraph by state, context, input and output. Only
#: the state type is ours to pin; the rest follow from the compiled graph.
type AgentStateGraph = StateGraph[AgentState, Any, Any, Any]

#: What LangGraph calls for each node. The return is a *partial* state, which is
#: expressible as ``AgentState`` precisely because it is declared ``total=False``.
type NodeFn = Callable[[AgentState], Awaitable[AgentState]]


def parse_graph_spec(spec: Any) -> list[GraphNode]:
    """Validate a ``graph_spec`` into an ordered node list.

    Graph specs are tenant-supplied data, so this rejects anything it does not
    understand rather than trying to coerce it. An empty or entrypoint-only spec
    is valid and yields a single decision node, which keeps older agent versions
    (written before graph specs carried nodes) executable.
    """
    if not isinstance(spec, dict):
        raise GraphSpecError("graph_spec must be an object")
    raw_nodes = spec.get("nodes")
    if raw_nodes is None:
        return [GraphNode(name=DEFAULT_NODE, kind="decision", tool=None, arguments={})]
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise GraphSpecError("graph_spec.nodes must be a non-empty list when present")

    nodes: list[GraphNode] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise GraphSpecError(f"graph_spec.nodes[{index}] must be an object")
        name = raw.get("name") or f"node_{index}"
        if not isinstance(name, str) or not name.strip():
            raise GraphSpecError(f"graph_spec.nodes[{index}].name must be a non-empty string")
        if name in seen:
            raise GraphSpecError(f"duplicate node name: {name}")
        seen.add(name)
        kind = raw.get("kind", "decision")
        if kind not in _SUPPORTED_NODE_KINDS:
            supported = ", ".join(sorted(_SUPPORTED_NODE_KINDS))
            raise GraphSpecError(
                f"graph_spec.nodes[{index}].kind must be one of: {supported}; got {kind!r}"
            )
        tool = raw.get("tool")
        if kind == "tool_call":
            if not isinstance(tool, str) or not tool.strip():
                raise GraphSpecError(
                    f"graph_spec.nodes[{index}] is a tool_call and needs a tool name"
                )
        elif tool is not None:
            raise GraphSpecError(f"graph_spec.nodes[{index}] is {kind} and must not name a tool")
        arguments = raw.get("arguments", {})
        if not isinstance(arguments, dict):
            raise GraphSpecError(f"graph_spec.nodes[{index}].arguments must be an object")
        nodes.append(GraphNode(name=name, kind=kind, tool=tool, arguments=arguments))
    return nodes


def build_graph(nodes: list[GraphNode], gateway: ToolGateway) -> AgentStateGraph:
    """Compile validated nodes into a linear LangGraph ``StateGraph``.

    The topology is intentionally linear. Branching graphs are a later phase;
    pretending to support them here would mean accepting specs this executor
    cannot faithfully run.
    """
    graph: AgentStateGraph = StateGraph(AgentState)

    for node in nodes:
        # add_node's overloads infer the node's input type from a concrete
        # function signature and cannot resolve it through a Callable alias.
        # The runtime contract is exercised by test_langgraph_executor.py.
        graph.add_node(node.name, _make_node_fn(node, gateway))  # type: ignore[call-overload]

    graph.add_edge(START, nodes[0].name)
    for current, following in pairwise(nodes):
        graph.add_edge(current.name, following.name)
    graph.add_edge(nodes[-1].name, END)
    return graph


def _make_node_fn(node: GraphNode, gateway: ToolGateway) -> NodeFn:
    """Build the callable LangGraph invokes for one node."""

    async def run_node(state: AgentState) -> AgentState:
        if node.kind == "tool_call":
            # `tool` is guaranteed present for tool_call nodes by parse_graph_spec.
            assert node.tool is not None
            result = await gateway.invoke(
                ToolInvocation(tool=node.tool, arguments=dict(node.arguments))
            )
            results = list(state.get("tool_results", ()))
            results.append(
                {"tool": result.tool, "output": result.output, "latency_ms": result.latency_ms}
            )
            return {"tool_results": results}
        if node.kind == "evidence":
            evidence = list(state.get("evidence", ()))
            evidence.append({"kind": "langgraph", "node": node.name, "supports": "passed"})
            return {"evidence": evidence}
        # A decision node reads what came before and commits to a verdict.
        return {"passed": True}

    return run_node


def checkpointer_conn_string(database_url: str) -> str:
    """Strip SQLAlchemy's driver suffix so psycopg accepts the URL.

    Settings carry ``postgresql+psycopg://`` for SQLAlchemy, but LangGraph's
    saver hands the string to psycopg directly, which rejects the ``+psycopg``.
    """
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


class LangGraphExecutor:
    """Runs an agent version's graph under LangGraph with durable checkpoints."""

    name = "langgraph"

    def __init__(self, *, database_url: str) -> None:
        self._conn_string = checkpointer_conn_string(database_url)

    async def execute(
        self,
        *,
        graph_spec: Any,
        gateway: ToolGateway,
        thread_id: str,
        item_index: int,
        partition: str,
    ) -> ExecutionOutcome:
        nodes = parse_graph_spec(graph_spec)
        async with AsyncPostgresSaver.from_conn_string(self._conn_string) as checkpointer:
            # Idempotent; LangGraph creates its own checkpoint tables. Kept here
            # rather than in an Alembic revision because the schema belongs to
            # LangGraph and moves with the dependency, not with our migrations.
            await checkpointer.setup()
            compiled = build_graph(nodes, gateway).compile(checkpointer=checkpointer)
            return await self._stream(
                compiled,
                nodes=nodes,
                thread_id=thread_id,
                item_index=item_index,
                partition=partition,
            )

    async def _stream(
        self,
        compiled: Any,
        *,
        nodes: list[GraphNode],
        thread_id: str,
        item_index: int,
        partition: str,
    ) -> ExecutionOutcome:
        kinds = {node.name: node.kind for node in nodes}
        config = {"configurable": {"thread_id": thread_id}}
        initial: AgentState = {
            "item_index": item_index,
            "partition": partition,
            "tool_results": [],
            "evidence": [],
            "passed": False,
        }
        outcome = ExecutionOutcome(thread_id=thread_id)
        merged: dict[str, Any] = dict(initial)

        # `updates` yields one payload per node as it completes: the event
        # capture hook. `values` would only ever give the accumulated state and
        # would lose which node produced what.
        async for update in compiled.astream(initial, config=config, stream_mode="updates"):
            for node_name, node_state in update.items():
                if isinstance(node_state, dict):
                    merged.update(node_state)
                outcome.events.append(
                    CapturedEvent(
                        node=node_name,
                        kind=kinds.get(node_name, "decision"),
                        state=dict(merged),
                    )
                )
        outcome.final_state = merged
        outcome.passed = bool(merged.get("passed", False))
        logger.info(
            "langgraph_execution_captured",
            extra={"thread_id": thread_id, "event_count": len(outcome.events)},
        )
        return outcome
