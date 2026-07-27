"""Phase 12: canary deployment promotion, rollback and history."""

from __future__ import annotations

import pytest
from api_test_support import Tenant
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.api.tests.test_release_api import (
    STRICT_POLICY,
    attach_report,
    create_policy,
    create_run,
)

pytestmark = pytest.mark.integration

BASELINE = {"success_rate": 0.99, "error_rate": 0.005, "p95_latency_ms": 120, "cost_per_1k": 0.10}
THRESHOLDS = {
    "min_success_rate": 0.95,
    "max_error_rate": 0.02,
    "max_p95_latency_delta_ms": 100,
    "max_cost_delta_per_1k": 0.05,
}


async def create_passing_gate(
    tenant: Tenant,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    run = await create_run(tenant)
    await attach_report(session_factory, run, pass_rate=0.98, regressions=0)
    policy = await create_policy(tenant, "Ship gate", STRICT_POLICY)
    gate = await tenant.client.post(
        f"/api/v1/evaluation-runs/{run['id']}/gate",
        json={"release_policy_id": policy["id"]},
    )
    assert gate.status_code == 200, gate.text
    assert gate.json()["outcome"] == "passed"
    return {"run_id": run["id"], "gate_evaluation_id": gate.json()["id"]}


class TestDeployments:
    async def test_healthy_candidate_promotes(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        gate = await create_passing_gate(tenant, session_factory)

        response = await tenant.client.post(
            "/api/v1/deployments",
            json={
                **gate,
                "traffic_percent": 10,
                "workload": {"source": "replay", "requests": 200},
                "baseline_metrics": BASELINE,
                "canary_metrics": {
                    "success_rate": 0.985,
                    "error_rate": 0.006,
                    "p95_latency_ms": 145,
                    "cost_per_1k": 0.11,
                },
                "thresholds": THRESHOLDS,
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["state"] == "promoted"
        assert body["traffic_percent"] == 100
        assert body["decision"]["decision"] == "promote"
        assert body["rollback_reason"] is None
        listed = await tenant.client.get(f"/api/v1/projects/{tenant.project_id}/deployments")
        assert listed.json()["items"][0]["id"] == body["id"]

    async def test_degraded_candidate_rolls_back(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        gate = await create_passing_gate(tenant, session_factory)

        response = await tenant.client.post(
            "/api/v1/deployments",
            json={
                **gate,
                "traffic_percent": 15,
                "workload": {"source": "replay", "requests": 200},
                "baseline_metrics": BASELINE,
                "canary_metrics": {
                    "success_rate": 0.91,
                    "error_rate": 0.05,
                    "p95_latency_ms": 280,
                    "cost_per_1k": 0.18,
                },
                "thresholds": THRESHOLDS,
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["state"] == "rolled_back"
        assert body["traffic_percent"] == 0
        assert body["decision"]["decision"] == "rollback"
        assert "success_rate" in body["rollback_reason"]
        assert body["rolled_back_at"] is not None

    async def test_blocked_gate_cannot_deploy(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_report(session_factory, run, pass_rate=0.75, regressions=2)
        policy = await create_policy(tenant, "Ship gate", STRICT_POLICY)
        gate = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )
        assert gate.json()["outcome"] == "blocked"

        response = await tenant.client.post(
            "/api/v1/deployments",
            json={
                "run_id": run["id"],
                "gate_evaluation_id": gate.json()["id"],
                "baseline_metrics": BASELINE,
                "canary_metrics": BASELINE,
                "thresholds": THRESHOLDS,
            },
        )

        assert response.status_code == 409

    async def test_cannot_list_another_tenants_deployments(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.get(
            f"/api/v1/projects/{other_tenant.project_id}/deployments"
        )

        assert response.status_code == 403
