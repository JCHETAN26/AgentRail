"""Seed a demo organisation with data the five-minute demo can actually show.

A fresh database renders an empty console, which makes the product impossible
to demonstrate and Phase 19's acceptance criterion — "an engineer can run the
demo, reproduce the benchmark, inspect a failure, and verify tribunal evidence"
— impossible to meet.

This drives the **public HTTP API**, the same way a user would, rather than
inserting rows. That matters: data written directly would not have passed
through validation, the run state machine, the trajectory capture or the
Tribunal, so it could look right in the console while differing from anything
the platform actually produces. Here the platform produces its own demo data;
this only supplies the inputs.

Two runs are seeded, because the comparison view is meaningless with one:

* a **baseline** run that passes cleanly, and
* a **candidate** run carrying an injected fault, so the demo has a failed item
  with a real trajectory and a real failing step to open.

Usage::

    uv run agentrail-seed                    # against http://localhost:8000
    uv run agentrail-seed --api-url ...      # against a deployed environment
    uv run agentrail-seed --reset            # new org each time, never destructive
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any

import httpx

#: Enough items to look like a real run without making the demo wait.
DEMO_ITEM_COUNT = 8

#: One item fails terminally. A retryable fault is no good here: it recovers on
#: the second attempt and leaves every item COMPLETED with no failing step, so
#: the demo has nothing to open. A refusal reproduces on retry and goes terminal
#: on the first occurrence, which is what puts a real failure in the trace.
DEMO_FAULT = {"kind": "model.refusal", "item_indexes": [3]}

#: Terminal run states, so waiting stops rather than hanging on a stuck run.
TERMINAL_RUN_STATES = {"PASSED", "FAILED", "CANCELLED", "ERROR"}


class SeedError(RuntimeError):
    """The seed could not complete. The message says which step and why."""


def dataset_jsonl(count: int) -> str:
    """A small incident dataset, one JSON object per line."""
    services = ("checkout-api", "billing-worker", "search-indexer", "notification-fanout")
    return "\n".join(
        (
            f'{{"id":"incident-{index}",'
            f'"input":{{"service":"{services[index % len(services)]}",'
            f'"symptom":"elevated error rate"}},'
            f'"expected":{{"ok":true}},'
            f'"partition":"p{index % 2}"}}'
        )
        for index in range(count)
    )


class SeedClient:
    """A thin wrapper that fails loudly and says which call failed."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.post(path, json=payload or {})
        if response.status_code >= 400:
            raise SeedError(f"POST {path} returned {response.status_code}: {response.text}")
        return dict(response.json())

    async def get(self, path: str) -> dict[str, Any]:
        response = await self._client.get(path)
        if response.status_code >= 400:
            raise SeedError(f"GET {path} returned {response.status_code}: {response.text}")
        return dict(response.json())


async def wait_for_run(seed: SeedClient, run_id: str, *, timeout_seconds: float) -> dict[str, Any]:
    """Poll until the run reaches a terminal state.

    The worker executes runs out of band, so seeding is not finished when the
    run is created. Returning early would leave the console showing a run stuck
    in RUNNING, which looks like a bug rather than a race.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        run = await seed.get(f"/api/v1/evaluation-runs/{run_id}")
        state = str(run.get("state", ""))
        if state in TERMINAL_RUN_STATES:
            return run
        if time.monotonic() > deadline:
            raise SeedError(
                f"run {run_id} was still {state} after {timeout_seconds:.0f}s. "
                "Is the worker running? Start it with: uv run agentrail-worker"
            )
        await asyncio.sleep(1.0)


async def seed_demo(
    *, api_url: str, email: str, organisation_name: str, wait_seconds: float
) -> dict[str, str]:
    async with httpx.AsyncClient(base_url=api_url, timeout=30.0, follow_redirects=True) as client:
        seed = SeedClient(client)

        # Dev-mode identity: deterministic, no OAuth round trip, no credentials.
        await seed.post("/api/v1/auth/dev/session", {"email": email})

        organisation = await seed.post("/api/v1/organisations", {"name": organisation_name})
        project = await seed.post(
            f"/api/v1/organisations/{organisation['id']}/projects",
            {"name": "Incident Response"},
        )

        agent = await seed.post(
            f"/api/v1/projects/{project['id']}/agents", {"name": "CloudOps Responder"}
        )
        baseline_version = await seed.post(
            f"/api/v1/agents/{agent['id']}/versions", _agent_version(revision="baseline")
        )
        candidate_version = await seed.post(
            f"/api/v1/agents/{agent['id']}/versions", _agent_version(revision="candidate")
        )

        dataset = await seed.post(
            f"/api/v1/projects/{project['id']}/datasets", {"name": "Incident Scenarios"}
        )
        dataset_version = await seed.post(
            f"/api/v1/datasets/{dataset['id']}/versions",
            {
                "input_format": "jsonl",
                "content": dataset_jsonl(DEMO_ITEM_COUNT),
                "source_filename": "incidents.jsonl",
            },
        )

        # Both runs share one suite, and they have to: a comparison resolves its
        # baseline by matching suite_digest, and that digest includes the suite
        # id, so two suites can never link no matter how alike they look. The
        # cost is that baseline and candidate see the same injected fault, so
        # the delta is zero — honest, and better than a comparison with no
        # baseline at all. A non-zero delta needs execution that varies with the
        # agent version, which the recorded path deliberately does not do.
        suite = await _frozen_suite(
            seed,
            project_id=project["id"],
            dataset_version_id=dataset_version["id"],
            name="Incident Response",
            fault_profiles=[DEMO_FAULT],
        )

        baseline_run = await seed.post(
            "/api/v1/evaluation-runs",
            {
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": baseline_version["id"],
            },
        )
        await wait_for_run(seed, baseline_run["id"], timeout_seconds=wait_seconds)

        # Naming the baseline version is what makes the comparison view show
        # deltas rather than the candidate's own numbers.
        candidate_run = await seed.post(
            "/api/v1/evaluation-runs",
            {
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate_version["id"],
                "baseline_agent_version_id": baseline_version["id"],
            },
        )
        final = await wait_for_run(seed, candidate_run["id"], timeout_seconds=wait_seconds)

        # Release evidence. The demo's fourth beat shows a gate verdict, and a
        # gate with nothing to judge cannot be shown. The floor is set above the
        # seeded pass rate deliberately: a gate that always passes demonstrates
        # nothing, and the interesting screen is the one where a rule blocks.
        policy = await seed.post(
            f"/api/v1/projects/{project['id']}/release-policies",
            {
                "name": "Ship gate",
                "definition": {"min_pass_rate": 0.95, "max_regressions": 0},
            },
        )
        gate = await seed.post(
            f"/api/v1/evaluation-runs/{candidate_run['id']}/gate",
            {"release_policy_id": policy["id"]},
        )

        return {
            "organisation": organisation["name"],
            "organisation_id": organisation["id"],
            "project_id": project["id"],
            "baseline_run_id": baseline_run["id"],
            "candidate_run_id": candidate_run["id"],
            "candidate_run_state": str(final.get("state", "")),
            "release_policy_id": policy["id"],
            "gate_outcome": str(gate.get("outcome", "")),
        }


def _agent_version(*, revision: str) -> dict[str, Any]:
    """An agent version the recorded executor can run.

    The two revisions differ in their prompt so their content digests differ —
    the registry rejects a duplicate version of the same agent, and a comparison
    between two identical versions would be meaningless anyway.
    """
    return {
        "graph_spec": {"entrypoint": "run"},
        "prompt_bundle": {
            "system": (
                "You are an on-call responder. Diagnose the incident and remediate it."
                if revision == "baseline"
                else "You are an on-call responder. Diagnose, remediate, then verify recovery."
            )
        },
        "model_config": {"provider": "recorded"},
        "tool_contracts": [
            {
                "name": "restart_service",
                "input_schema": {"type": "object"},
                "risk_level": "HIGH_RISK_WRITE",
            },
            {
                "name": "search_logs",
                "input_schema": {"type": "object"},
                "risk_level": "READ_ONLY",
            },
        ],
        "policy_bundle": {
            "tool_risks": {"restart_service": "LOW_RISK_WRITE", "search_logs": "READ_ONLY"}
        },
    }


async def _frozen_suite(
    seed: SeedClient,
    *,
    project_id: str,
    dataset_version_id: str,
    name: str,
    fault_profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    suite = await seed.post(
        f"/api/v1/projects/{project_id}/evaluation-suites",
        {
            "name": name,
            "dataset_version_id": dataset_version_id,
            "evaluators": [{"name": "task_success", "threshold": 1.0}],
            "fault_profiles": fault_profiles,
            # The Tribunal is the flagship; a demo without a verdict skips it.
            "thresholds": {"task_success": 1.0, "tribunal": {"enabled": True}},
        },
    )
    return await seed.post(f"/api/v1/evaluation-suites/{suite['id']}/freeze")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed AgentRail with demo data.")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--email", default="demo@agentrail.dev")
    parser.add_argument("--organisation", default="Demo Labs")
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=120.0,
        help="How long to wait for each run to finish before giving up.",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(
            seed_demo(
                api_url=args.api_url,
                email=args.email,
                organisation_name=args.organisation,
                wait_seconds=args.wait_seconds,
            )
        )
    except SeedError as failure:
        print(f"seed failed: {failure}", file=sys.stderr)
        return 1
    except httpx.HTTPError as unreachable:
        print(
            f"seed failed: could not reach {args.api_url} ({unreachable}). "
            "Is the API running? Start it with: uv run agentrail-api",
            file=sys.stderr,
        )
        return 1

    print(f"Seeded {result['organisation']} ({result['organisation_id']})")
    print(f"  project           {result['project_id']}")
    print(f"  baseline run      {result['baseline_run_id']}")
    print(f"  candidate run     {result['candidate_run_id']}  [{result['candidate_run_state']}]")
    print(f"  release gate      {result['gate_outcome']}  (policy {result['release_policy_id']})")
    print()
    print("Open the console and paste the candidate run id into the trace explorer")
    print("and the Tribunal panel.")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
