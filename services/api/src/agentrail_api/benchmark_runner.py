"""Drive real evaluation runs and measure what actually happened.

This is the data source the benchmark report used to fabricate. It creates a
project, a frozen suite and an evaluation run through the **public HTTP API**,
waits for the worker to execute them, then reads back the persisted rows and
turns them into measurements.

What is observed, and from where:

* ``task_success`` — the run item's terminal state. Either the platform
  completed the item or it did not.
* ``programmatic_pass`` — the persisted ``EvaluationResult`` state for that
  item, which is what the evaluators actually decided.
* ``tribunal_approve`` — the persisted Tribunal verdict for the run.
* ``duration_ms`` — wall clock between the item starting and completing,
  measured from timestamps the worker wrote.

What is deliberately **not** reported: tokens and cost. The recorded executor
makes no model calls, so there is nothing to count, and a benchmark that
published them would be publishing an assumption. They return when a live model
provider is used, measured from its response.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from agentrail_core.benchmarks import BenchmarkFamily, BenchmarkScenario

#: Terminal run states, so waiting stops rather than hanging.
TERMINAL_RUN_STATES = {"PASSED", "FAILED", "CANCELLED", "ERROR"}

#: Faults injected per benchmark family. The families are not different data —
#: they are the same pipeline under different pressure, which is the only
#: difference that can be measured rather than asserted.
FAMILY_FAULTS: dict[BenchmarkFamily, list[dict[str, Any]]] = {
    "smoke": [],
    "quality": [],
    "failures": [{"kind": "model.refusal", "every_n": 4}],
    "tribunal": [],
    "load": [{"kind": "tool.latency", "every_n": 3, "attempts": [1]}],
}

#: Whether the family convenes a Tribunal. Measuring its cost means running
#: comparable suites with and without it.
FAMILY_TRIBUNAL: dict[BenchmarkFamily, bool] = {
    "smoke": False,
    "quality": True,
    "failures": True,
    "tribunal": True,
    "load": False,
}


class BenchmarkRunError(RuntimeError):
    """The benchmark could not be measured. The message says why."""


@dataclass(frozen=True, slots=True)
class MeasuredRun:
    """One executed run and everything read back from it."""

    run_id: str
    items: list[dict[str, Any]]
    results: list[dict[str, Any]]
    tribunal_outcome: str | None


def _parse(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def item_duration_ms(item: dict[str, Any]) -> int:
    """Wall clock for one item, from the timestamps the worker wrote.

    Zero when an item never started — a parked or cancelled item has no
    duration, and inventing one would be the habit this module exists to end.
    """
    started = _parse(item.get("started_at"))
    completed = _parse(item.get("completed_at")) or _parse(item.get("updated_at"))
    if started is None or completed is None:
        return 0
    return max(0, int((completed - started).total_seconds() * 1000))


def measure(
    benchmark: BenchmarkFamily, run: MeasuredRun, *, scenario_families: list[str]
) -> list[BenchmarkScenario]:
    """Turn one executed run into one measured scenario per item."""
    results_by_item = {result["run_item_id"]: result for result in run.results}
    tribunal_enabled = run.tribunal_outcome is not None
    approved = run.tribunal_outcome == "approved"

    scenarios: list[BenchmarkScenario] = []
    for index, item in enumerate(sorted(run.items, key=lambda row: row["item_index"])):
        result = results_by_item.get(item["id"])
        programmatic_pass = bool(result and result["state"] == "PASSED")
        task_success = item["state"] == "COMPLETED"
        # A run-level verdict applies to each of its items; without a Tribunal
        # the evaluators' own decision stands, so consensus is trivially true
        # and is reported that way rather than being quietly counted as agreement.
        tribunal_approve = approved if tribunal_enabled else programmatic_pass
        scenarios.append(
            BenchmarkScenario(
                scenario_id=f"{benchmark}-{run.run_id}-{item['item_index']:04d}",
                family=scenario_families[index % len(scenario_families)],
                benchmark=benchmark,
                tribunal_enabled=tribunal_enabled,
                duration_ms=item_duration_ms(item),
                task_success=task_success,
                programmatic_pass=programmatic_pass,
                tribunal_approve=tribunal_approve,
                consensus=programmatic_pass == tribunal_approve,
            )
        )
    return scenarios


class BenchmarkClient:
    """HTTP client that fails loudly and names the call that failed."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.post(path, json=payload or {})
        if response.status_code >= 400:
            raise BenchmarkRunError(f"POST {path} -> {response.status_code}: {response.text}")
        return dict(response.json())

    async def get(self, path: str) -> dict[str, Any]:
        response = await self._client.get(path)
        if response.status_code >= 400:
            raise BenchmarkRunError(f"GET {path} -> {response.status_code}: {response.text}")
        return dict(response.json())


async def run_benchmark(
    benchmark: BenchmarkFamily,
    *,
    api_url: str,
    item_count: int,
    scenario_families: list[str],
    email: str = "benchmark@agentrail.dev",
    wait_seconds: float = 600.0,
) -> list[BenchmarkScenario]:
    """Execute one benchmark family for real and return its measurements."""
    async with httpx.AsyncClient(base_url=api_url, timeout=60.0, follow_redirects=True) as raw:
        client = BenchmarkClient(raw)
        await client.post("/api/v1/auth/dev/session", {"email": email})

        organisation = await client.post(
            "/api/v1/organisations", {"name": f"Benchmark {benchmark} {int(time.time())}"}
        )
        project = await client.post(
            f"/api/v1/organisations/{organisation['id']}/projects", {"name": f"bench-{benchmark}"}
        )
        agent = await client.post(
            f"/api/v1/projects/{project['id']}/agents", {"name": "Benchmark Agent"}
        )
        version = await client.post(
            f"/api/v1/agents/{agent['id']}/versions",
            {
                "graph_spec": {"entrypoint": "run"},
                "prompt_bundle": {"system": f"benchmark {benchmark}"},
                "model_config": {"provider": "recorded"},
                "tool_contracts": [],
                "policy_bundle": {"tool_risks": {"restart_service": "LOW_RISK_WRITE"}},
            },
        )
        dataset = await client.post(
            f"/api/v1/projects/{project['id']}/datasets", {"name": f"bench-{benchmark}"}
        )
        content = "\n".join(
            f'{{"id":"bench-{index}","input":{{"service":"svc-{index}"}},'
            f'"expected":{{"ok":true}},"partition":"p{index % 2}"}}'
            for index in range(item_count)
        )
        dataset_version = await client.post(
            f"/api/v1/datasets/{dataset['id']}/versions",
            {"input_format": "jsonl", "content": content},
        )
        thresholds: dict[str, Any] = {"task_success": 1.0}
        if FAMILY_TRIBUNAL[benchmark]:
            thresholds["tribunal"] = {"enabled": True}
        suite = await client.post(
            f"/api/v1/projects/{project['id']}/evaluation-suites",
            {
                "name": f"bench-{benchmark}",
                "dataset_version_id": dataset_version["id"],
                "evaluators": [{"name": "task_success", "threshold": 1.0}],
                "fault_profiles": FAMILY_FAULTS[benchmark],
                "thresholds": thresholds,
            },
        )
        suite = await client.post(f"/api/v1/evaluation-suites/{suite['id']}/freeze")

        run = await client.post(
            "/api/v1/evaluation-runs",
            {
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": version["id"],
            },
        )
        await _wait(client, run["id"], wait_seconds=wait_seconds)

        items = (await client.get(f"/api/v1/evaluation-runs/{run['id']}/items"))["items"]
        results = (await client.get(f"/api/v1/evaluation-runs/{run['id']}/evaluator-results"))[
            "items"
        ]
        tribunal_outcome: str | None = None
        if FAMILY_TRIBUNAL[benchmark]:
            tribunal = await client.get(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
            tribunal_outcome = str(tribunal.get("outcome"))

        measured = MeasuredRun(
            run_id=run["id"], items=items, results=results, tribunal_outcome=tribunal_outcome
        )
        return measure(benchmark, measured, scenario_families=scenario_families)


async def _wait(client: BenchmarkClient, run_id: str, *, wait_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + wait_seconds
    while True:
        run = await client.get(f"/api/v1/evaluation-runs/{run_id}")
        if str(run.get("state")) in TERMINAL_RUN_STATES:
            return run
        if time.monotonic() > deadline:
            raise BenchmarkRunError(
                f"run {run_id} was still {run.get('state')} after {wait_seconds:.0f}s. "
                "Is the worker running? Start it with: uv run agentrail-worker"
            )
        await asyncio.sleep(1.0)
