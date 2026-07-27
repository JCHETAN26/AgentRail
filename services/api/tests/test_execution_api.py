"""Evaluation-run API integration tests."""

from __future__ import annotations

import pytest
from api_test_support import Tenant

pytestmark = pytest.mark.integration


def dataset_content(count: int = 3) -> str:
    return "\n".join(
        (
            f'{{"id":"case-{index}","input":{{"service":"checkout"}},'
            f'"expected":{{"ok":true}},"partition":"p{index % 2}"}}'
        )
        for index in range(count)
    )


async def create_agent_version(tenant: Tenant, name: str = "Runner Agent") -> dict[str, object]:
    agent = await tenant.client.post(
        f"/api/v1/projects/{tenant.project_id}/agents",
        json={"name": name},
    )
    assert agent.status_code == 201, agent.text
    version = await tenant.client.post(
        f"/api/v1/agents/{agent.json()['id']}/versions",
        json={
            "graph_spec": {"entrypoint": "run"},
            "prompt_bundle": {"system": "test"},
            "model_config": {"provider": "recorded"},
        },
    )
    assert version.status_code == 201, version.text
    return version.json()


async def create_frozen_suite(
    tenant: Tenant, *, count: int = 3, frozen: bool = True
) -> dict[str, object]:
    dataset = await tenant.client.post(
        f"/api/v1/projects/{tenant.project_id}/datasets",
        json={"name": f"Run Data {count} {frozen}"},
    )
    assert dataset.status_code == 201, dataset.text
    version = await tenant.client.post(
        f"/api/v1/datasets/{dataset.json()['id']}/versions",
        json={"input_format": "jsonl", "content": dataset_content(count)},
    )
    assert version.status_code == 201, version.text
    suite = await tenant.client.post(
        f"/api/v1/projects/{tenant.project_id}/evaluation-suites",
        json={
            "name": f"Run Suite {count} {frozen}",
            "dataset_version_id": version.json()["id"],
            "evaluators": [{"name": "recorded_success"}],
            "thresholds": {"task_success": 1.0},
        },
    )
    assert suite.status_code == 201, suite.text
    if not frozen:
        return suite.json()
    freeze = await tenant.client.post(f"/api/v1/evaluation-suites/{suite.json()['id']}/freeze")
    assert freeze.status_code == 200, freeze.text
    return freeze.json()


class TestEvaluationRuns:
    async def test_creates_run_items_from_a_frozen_suite(self, tenant: Tenant) -> None:
        suite = await create_frozen_suite(tenant, count=4)
        candidate = await create_agent_version(tenant)

        created = await tenant.client.post(
            "/api/v1/evaluation-runs",
            headers={"Idempotency-Key": "run-1"},
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
            },
        )
        replay = await tenant.client.post(
            "/api/v1/evaluation-runs",
            headers={"Idempotency-Key": "run-1"},
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
            },
        )

        assert created.status_code == 201, created.text
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == created.json()["id"]
        assert created.json()["state"] == "CREATED"
        assert created.json()["item_count"] == 4
        assert created.json()["summary"]["partition_counts"] == {"p0": 2, "p1": 2}

    async def test_rejects_unfrozen_suite(self, tenant: Tenant) -> None:
        suite = await create_frozen_suite(tenant, frozen=False)
        candidate = await create_agent_version(tenant, "Unfrozen Candidate")

        response = await tenant.client.post(
            "/api/v1/evaluation-runs",
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
            },
        )

        assert response.status_code == 422

    async def test_cannot_run_another_tenants_suite(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        suite = await create_frozen_suite(other_tenant)
        candidate = await create_agent_version(tenant, "Tenant Candidate")

        response = await tenant.client.post(
            "/api/v1/evaluation-runs",
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
            },
        )

        assert response.status_code == 403

    async def test_cancel_is_idempotent(self, tenant: Tenant) -> None:
        suite = await create_frozen_suite(tenant)
        candidate = await create_agent_version(tenant, "Cancel Candidate")
        created = await tenant.client.post(
            "/api/v1/evaluation-runs",
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
            },
        )
        assert created.status_code == 201, created.text

        first = await tenant.client.post(f"/api/v1/evaluation-runs/{created.json()['id']}/cancel")
        second = await tenant.client.post(f"/api/v1/evaluation-runs/{created.json()['id']}/cancel")

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["state"] == "CANCELLED"
        assert second.json()["cancelled_at"] == first.json()["cancelled_at"]

    async def test_recovery_view_reports_attempts_leases_and_side_effects(
        self, tenant: Tenant
    ) -> None:
        suite = await create_frozen_suite(tenant, count=2)
        candidate = await create_agent_version(tenant, "Recovery Candidate")
        created = await tenant.client.post(
            "/api/v1/evaluation-runs",
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
            },
        )
        assert created.status_code == 201, created.text

        response = await tenant.client.get(
            f"/api/v1/evaluation-runs/{created.json()['id']}/recovery"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == created.json()["id"]
        assert len(body["items"]) == 2
        # Nothing has executed yet, so nothing is stranded and nothing has acted.
        assert body["stranded_count"] == 0
        assert body["retried_count"] == 0
        assert body["side_effect_count"] == 0
        assert body["items"][0]["retries_remaining"] == body["items"][0]["max_attempts"]
        assert body["items"][0]["lease_expired"] is False

    async def test_cannot_read_another_tenants_recovery_view(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        suite = await create_frozen_suite(other_tenant)
        candidate = await create_agent_version(other_tenant, "Theirs")
        created = await other_tenant.client.post(
            "/api/v1/evaluation-runs",
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
            },
        )
        assert created.status_code == 201, created.text

        response = await tenant.client.get(
            f"/api/v1/evaluation-runs/{created.json()['id']}/recovery"
        )

        assert response.status_code == 403
