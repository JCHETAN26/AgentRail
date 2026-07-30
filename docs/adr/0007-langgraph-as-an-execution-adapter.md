# ADR 0007 — LangGraph is an execution adapter, not the execution model

- **Status:** Accepted
- **Date:** 2026-07-29
- **Phase:** 5

## Context

`BUILDPLAN.md` names LangGraph as the orchestration engine for persisted graph execution,
checkpoints, replay and interrupts, and puts replacing it out of scope. Until this ADR, that
commitment was unmet: `langgraph` was not a dependency, nothing imported it, and the
`LangGraphAdapter` in `agentrail_core.agents` was a marker class that validated `graph_spec` was a
dict and executed nothing. Every run item went through one hard-coded pipeline inside
`run_runner._execute_item` that emitted a fixed five-step trajectory.

Three checklist items across Phases 5, 6 and 11 depend on real LangGraph — a PostgreSQL
checkpointer, an event capture hook, and interrupts for high-risk tool calls. None of them can be
honestly checked against a marker class.

At the same time, two properties of this platform are non-negotiable and sit in tension with adopting
a framework:

1. **Determinism.** CI, the frozen benchmark and the demo must produce byte-identical results. A
   framework that reaches for a model provider or a clock breaks reproducibility.
2. **Enforceable guarantees.** Budget ledgers, the policy gate, human approval and the idempotent
   side-effect ledger are platform promises. An agent must not be able to bypass them, and "the
   framework calls the tool directly" is exactly how that bypass happens.

## Decision

LangGraph is adopted as **one adapter behind a seam**, not as the execution model everything else is
written against.

- `langgraph` and `langgraph-checkpoint-postgres` are dependencies of **`services/worker` only**.
  `agentrail-core` does not depend on them, satisfying `BUILDPLAN.md`'s requirement that the domain
  not depend on an agent framework. Evaluation, policy, tribunal and trajectory logic keep working
  with no framework installed.
- `agentrail_worker.langgraph_executor` is the only module in the repository that imports LangGraph.
- **Tool calls are inverted.** The graph never invokes a tool. It calls a `ToolGateway` the runner
  supplies, and that implementation charges the budget ledger, runs the policy gate and applies the
  effect through the idempotent side-effect ledger. A graph therefore cannot skip a platform
  guarantee by construction, rather than by convention.
- **Checkpoints are LangGraph's own.** The graph is compiled with `AsyncPostgresSaver` against the
  same PostgreSQL instance, keyed by a thread id derived from the run item. LangGraph owns those
  tables and creates them through its `setup()`, not through an Alembic revision, because the schema
  moves with the dependency rather than with our migrations.
- **The event capture hook is `astream(stream_mode="updates")`.** That yields one payload per node as
  it completes, so each node transition becomes a `TrajectoryStep` carrying the state that node
  produced. `stream_mode="values"` would only ever give accumulated state and would lose which node
  produced what — which is precisely the information the trace explorer's per-step graph state needs.
- **The recorded path stays the default.** Deterministic and recorded execution remain untouched and
  continue to serve CI, the benchmark and the demo. LangGraph is selected per agent version.

## Graph specs are tenant data

`graph_spec` arrives from an API caller. `parse_graph_spec` therefore validates rather than coerces:
unknown node kinds, tool-call nodes without a tool, non-tool nodes naming a tool, duplicate node
names and non-object arguments are all rejected. Only three node kinds exist (`tool_call`,
`evidence`, `decision`), and a spec can never nominate arbitrary Python to execute.

Topology is linear for now. Branching is a later phase; accepting branching specs this executor
would silently flatten is worse than refusing them.

## Alternatives considered

- **Amend the plan and keep the in-house runner.** Defensible on determinism grounds, and the
  in-house runner is genuinely simpler. Rejected because the project's stated architecture names
  LangGraph, and a portfolio piece whose headline claim is unsupported by its code has a credibility
  problem that no amount of internal elegance fixes.
- **Replace the recorded pipeline with LangGraph outright.** Rejected. The recorded path is what
  makes CI and the frozen benchmark reproducible, and `_execute_item` also owns leases, retries,
  approval parking and idempotency. Rewriting it in one step would put those guarantees at risk for
  no gain, since both paths are needed anyway.
- **Let graph nodes call tools directly and audit afterwards.** Rejected. Detection is not
  prevention: a side effect that reached the world before the policy gate ran cannot be un-applied.
- **Put LangGraph in `agentrail-core` so the API can compile graphs too.** Rejected: it would make
  the domain depend on an agent framework, contradicting `BUILDPLAN.md:705`, and the API has no
  reason to execute a graph.
- **Our own checkpoint tables for LangGraph state.** Rejected. We would be reimplementing a
  dependency's persistence and would have to track its format across upgrades. Our
  `TrajectoryCheckpoint` rows remain the _platform's_ record; LangGraph's tables are its own
  resumption substrate. The two answer different questions.

## Consequences

- The worker image grows by LangGraph and its transitive dependencies (`langchain-core`, `langsmith`,
  `orjson`, `xxhash` and others). `psycopg` and `pydantic` resolved unchanged at their existing pins;
  `websockets` was pulled back from 16.1.1 to 15.0.1, which is inert because nothing in this
  repository uses websockets.
- LangGraph creates and owns tables in the application database. An operator inspecting the schema
  will find tables no Alembic revision produced; this ADR is the explanation.
- `add_node` carries one `type: ignore[call-overload]`: its overloads infer a node's input type from
  a concrete function signature and cannot resolve it through a `Callable` type alias. The runtime
  contract is covered by tests instead.
- Two execution paths now exist. Any new platform guarantee must be added to both, or added to the
  runner above the seam — the latter is preferred and is where the gateway already sits.
