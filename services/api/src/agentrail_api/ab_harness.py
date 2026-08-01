"""A/B discrimination: does the release gate actually tell good from bad?

Every other measurement in this repository describes the platform — how fast it
orchestrates, whether effects apply once, whether a biased role can flip a
verdict. None of them answer the question the product exists for:

    Given an agent version that is genuinely worse, does the gate block it?
    Given one that is unchanged, does the gate leave it alone?

A gate that blocks everything scores perfectly on the first and is useless. A
gate that blocks nothing scores perfectly on the second and is worse than
useless. Both numbers only mean something together, which is why this reports
them as a pair and never separately.

The method is a controlled comparison. Two agent versions run the *same frozen
suite* under the *same release policy*; the only difference is the agent. A
control arm submits an unchanged version to measure false blocks — without it,
"blocked 10 of 10 regressions" is indistinguishable from a gate that is simply
broken shut.

Regressions are injected, not hoped for. Each variant below degrades the agent
in a way an operator would recognise, so a block is attributable to a specific
defect rather than to noise.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

TERMINAL_RUN_STATES = {"PASSED", "FAILED", "CANCELLED", "ERROR"}

#: The control. Identical to the baseline in every respect; any block here is a
#: false positive, and a gate that produces them gets switched off by the team
#: it protects.
CONTROL = "unchanged"

#: Ways an agent version is made genuinely worse. Each maps to a fault the
#: platform can inject deterministically, so the comparison is repeatable and a
#: block can be traced to the defect that caused it.
REGRESSIONS: dict[str, list[dict[str, Any]]] = {
    "refuses_to_act": [{"kind": "model.refusal", "every_n": 2}],
    "wrong_tool": [{"kind": "model.wrong_tool", "every_n": 2}],
    "invalid_arguments": [{"kind": "model.invalid_arguments", "every_n": 3}],
    "loops_without_concluding": [{"kind": "model.tool_loop", "every_n": 3}],
    "times_out": [{"kind": "model.timeout", "every_n": 4}],
}

#: Degradation small enough to sit inside the policy's tolerance. These *should
#: pass*, and they are the arms that make the result mean anything: without
#: them, a gate wired shut would score a perfect detection rate. A gate that
#: blocks these is not sensitive, it is broken.
WITHIN_TOLERANCE: dict[str, list[dict[str, Any]]] = {
    "occasional_timeout": [{"kind": "model.timeout", "every_n": 16}],
}

#: Deliberately below 1.0. A threshold of 1.0 blocks any imperfection at all,
#: which makes "caught every regression" true and uninformative — it would not
#: distinguish a working gate from one that refuses everything.
MIN_PASS_RATE = 0.85


class ABHarnessError(RuntimeError):
    """The comparison could not be run. The message says which step failed."""


@dataclass(frozen=True, slots=True)
class ArmResult:
    """One agent version's journey through the gate."""

    arm: str
    regressed: bool
    run_id: str
    pass_rate: float
    gate_outcome: str
    tribunal_outcome: str
    verdict_ms: int

    @property
    def blocked(self) -> bool:
        return self.gate_outcome == "blocked"


@dataclass(frozen=True, slots=True)
class DiscriminationReport:
    """What the gate got right, and what it got wrong."""

    arms: list[ArmResult]

    @property
    def regressed(self) -> list[ArmResult]:
        return [arm for arm in self.arms if arm.regressed]

    @property
    def threshold(self) -> float:
        return MIN_PASS_RATE

    @property
    def controls(self) -> list[ArmResult]:
        return [arm for arm in self.arms if not arm.regressed]

    @property
    def caught(self) -> int:
        """Regressions the gate blocked. Recall."""
        return sum(1 for arm in self.regressed if arm.blocked)

    @property
    def false_blocks(self) -> int:
        """Unchanged versions the gate blocked. The cost of being wrong."""
        return sum(1 for arm in self.controls if arm.blocked)

    def as_summary(self) -> dict[str, Any]:
        return {
            "min_pass_rate": MIN_PASS_RATE,
            "regressions_submitted": len(self.regressed),
            "regressions_blocked": self.caught,
            "controls_submitted": len(self.controls),
            "controls_blocked": self.false_blocks,
            "detection_rate": self.caught / len(self.regressed) if self.regressed else 0.0,
            "false_block_rate": (self.false_blocks / len(self.controls) if self.controls else 0.0),
            "tribunal_approved_controls": sum(
                1 for arm in self.controls if arm.tribunal_outcome == "approved"
            ),
            "tribunal_approved_regressions": sum(
                1 for arm in self.regressed if arm.tribunal_outcome == "approved"
            ),
            "verdict_ms_median": sorted(arm.verdict_ms for arm in self.arms)[len(self.arms) // 2]
            if self.arms
            else 0,
            "arms": [
                {
                    "arm": arm.arm,
                    "tribunal": arm.tribunal_outcome,
                    "verdict_ms": arm.verdict_ms,
                    "regressed": arm.regressed,
                    "pass_rate": arm.pass_rate,
                    "gate": arm.gate_outcome,
                }
                for arm in self.arms
            ],
        }


class _Client:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.post(path, json=payload or {})
        if response.status_code >= 400:
            raise ABHarnessError(f"POST {path} -> {response.status_code}: {response.text}")
        return dict(response.json())

    async def get(self, path: str) -> dict[str, Any]:
        response = await self._client.get(path)
        if response.status_code >= 400:
            raise ABHarnessError(f"GET {path} -> {response.status_code}: {response.text}")
        return dict(response.json())


def _agent_version(revision: str) -> dict[str, Any]:
    """An agent version. The prompt differs so the content digests differ.

    The registry refuses a duplicate version of the same agent, and comparing a
    version against itself would measure nothing anyway.
    """
    return {
        "graph_spec": {"entrypoint": "run"},
        "prompt_bundle": {"system": f"on-call responder ({revision})"},
        "model_config": {"provider": "recorded"},
        "tool_contracts": [
            {
                "name": "restart_service",
                "input_schema": {"type": "object"},
                "risk_level": "LOW_RISK_WRITE",
            }
        ],
        "policy_bundle": {"tool_risks": {"restart_service": "LOW_RISK_WRITE"}},
    }


async def _wait(client: _Client, run_id: str, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        run = await client.get(f"/api/v1/evaluation-runs/{run_id}")
        if str(run.get("state")) in TERMINAL_RUN_STATES:
            return run
        if time.monotonic() > deadline:
            raise ABHarnessError(
                f"run {run_id} still {run.get('state')} after {timeout_seconds:.0f}s; "
                "is the worker running?"
            )
        await asyncio.sleep(1.0)


async def run_discrimination(
    *,
    api_url: str,
    item_count: int = 12,
    controls: int = 5,
    email: str = "ab@agentrail.dev",
    wait_seconds: float = 300.0,
) -> DiscriminationReport:
    """Submit regressed and unchanged agent versions to the same gate."""
    async with httpx.AsyncClient(base_url=api_url, timeout=60.0, follow_redirects=True) as raw:
        client = _Client(raw)
        await client.post("/api/v1/auth/dev/session", {"email": email})
        organisation = await client.post(
            "/api/v1/organisations", {"name": f"AB {int(time.time())}"}
        )
        project = await client.post(
            f"/api/v1/organisations/{organisation['id']}/projects", {"name": "ab"}
        )
        agent = await client.post(f"/api/v1/projects/{project['id']}/agents", {"name": "Responder"})
        dataset = await client.post(
            f"/api/v1/projects/{project['id']}/datasets", {"name": "incidents"}
        )
        content = "\n".join(
            f'{{"id":"inc-{index}","input":{{"service":"svc-{index}",'
            f'"symptom":"elevated errors"}},"expected":{{"ok":true}},'
            f'"partition":"p{index % 2}"}}'
            for index in range(item_count)
        )
        dataset_version = await client.post(
            f"/api/v1/datasets/{dataset['id']}/versions",
            {"input_format": "jsonl", "content": content},
        )
        # One policy for every arm. A gate judged against different rules per
        # variant would not be a comparison.
        policy = await client.post(
            f"/api/v1/projects/{project['id']}/release-policies",
            {
                "name": "ship-gate",
                "definition": {"min_pass_rate": MIN_PASS_RATE},
            },
        )

        arms: list[ArmResult] = []
        variants: list[tuple[str, list[dict[str, Any]]]] = [(CONTROL, []) for _ in range(controls)]
        variants += list(REGRESSIONS.items())
        # Tolerance arms are scored as controls: they are expected to pass, so
        # a block here counts against the gate exactly as an unchanged version
        # would. Without them a gate wired shut scores a perfect detection rate.
        variants += list(WITHIN_TOLERANCE.items())
        for index, (arm, faults) in enumerate(variants):
            arms.append(
                await _run_arm(
                    client,
                    project_id=project["id"],
                    agent_id=agent["id"],
                    dataset_version_id=dataset_version["id"],
                    policy_id=policy["id"],
                    arm=arm,
                    faults=faults,
                    revision=f"{arm}-{index}",
                    wait_seconds=wait_seconds,
                )
            )
        return DiscriminationReport(arms=arms)


async def _run_arm(
    client: _Client,
    *,
    project_id: str,
    agent_id: str,
    dataset_version_id: str,
    policy_id: str,
    arm: str,
    faults: list[dict[str, Any]],
    revision: str,
    wait_seconds: float,
) -> ArmResult:
    version = await client.post(f"/api/v1/agents/{agent_id}/versions", _agent_version(revision))
    suite = await client.post(
        f"/api/v1/projects/{project_id}/evaluation-suites",
        {
            "name": f"suite-{revision}",
            "dataset_version_id": dataset_version_id,
            "evaluators": [{"name": "task_success", "threshold": 1.0}],
            "fault_profiles": faults,
            "thresholds": {"task_success": 1.0, "tribunal": {"enabled": True}},
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
    await _wait(client, run["id"], timeout_seconds=wait_seconds)
    # Time the verdict fetch, not the whole run: what an operator waits for
    # after the evidence exists is the panel, and that is the cost a release
    # gate adds to a merge.
    started = time.monotonic()
    tribunal = await client.get(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
    verdict_ms = int((time.monotonic() - started) * 1000)
    comparison = await client.get(f"/api/v1/evaluation-runs/{run['id']}/comparison")
    gate = await client.post(
        f"/api/v1/evaluation-runs/{run['id']}/gate", {"release_policy_id": policy_id}
    )
    return ArmResult(
        arm=arm,
        regressed=arm not in {CONTROL, *WITHIN_TOLERANCE},
        run_id=run["id"],
        pass_rate=float(comparison.get("summary", {}).get("pass_rate") or 0.0),
        gate_outcome=str(gate.get("outcome") or ""),
        tribunal_outcome=str(tribunal.get("outcome") or ""),
        verdict_ms=verdict_ms,
    )
