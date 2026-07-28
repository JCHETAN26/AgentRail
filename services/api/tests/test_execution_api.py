"""Evaluation-run API integration tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api_test_support import Tenant
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.evaluators import ComparisonReport
from agentrail_core.execution import OutboxEvent
from agentrail_core.ids import new_sortable_id
from agentrail_core.quotas import OrganisationQuotaPeriod

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
    tenant: Tenant,
    *,
    count: int = 3,
    frozen: bool = True,
    thresholds: dict[str, object] | None = None,
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
            "thresholds": thresholds or {"task_success": 1.0},
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

    async def test_run_creation_spends_a_durable_organisation_quota(
        self,
        integration_app: FastAPI,
        tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        original_settings = integration_app.state.settings
        integration_app.state.settings = original_settings.model_copy(
            update={"evaluation_item_monthly_quota": 3}
        )
        try:
            suite = await create_frozen_suite(tenant, count=2)
            candidate = await create_agent_version(tenant, "Quota Candidate")

            first = await tenant.client.post(
                "/api/v1/evaluation-runs",
                json={
                    "evaluation_suite_id": suite["id"],
                    "candidate_agent_version_id": candidate["id"],
                },
            )
            second = await tenant.client.post(
                "/api/v1/evaluation-runs",
                json={
                    "evaluation_suite_id": suite["id"],
                    "candidate_agent_version_id": candidate["id"],
                },
            )
        finally:
            integration_app.state.settings = original_settings

        assert first.status_code == 201, first.text
        assert second.status_code == 429
        assert second.json()["code"] == "quota_exceeded"
        assert second.json()["details"]["limit"] == 3
        assert second.json()["details"]["used"] == 2
        assert second.json()["details"]["requested"] == 2

        async with session_factory() as session:
            periods = list(
                (
                    await session.execute(
                        select(OrganisationQuotaPeriod).where(
                            OrganisationQuotaPeriod.organisation_id == tenant.organisation_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(periods) == 1
        assert periods[0].evaluation_item_limit == 3
        assert periods[0].evaluation_items_used == 2

    async def test_idempotent_run_replay_does_not_spend_quota_again(
        self,
        integration_app: FastAPI,
        tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        original_settings = integration_app.state.settings
        integration_app.state.settings = original_settings.model_copy(
            update={"evaluation_item_monthly_quota": 2}
        )
        try:
            suite = await create_frozen_suite(tenant, count=2)
            candidate = await create_agent_version(tenant, "Quota Replay Candidate")

            first = await tenant.client.post(
                "/api/v1/evaluation-runs",
                headers={"Idempotency-Key": "quota-replay"},
                json={
                    "evaluation_suite_id": suite["id"],
                    "candidate_agent_version_id": candidate["id"],
                },
            )
            replay = await tenant.client.post(
                "/api/v1/evaluation-runs",
                headers={"Idempotency-Key": "quota-replay"},
                json={
                    "evaluation_suite_id": suite["id"],
                    "candidate_agent_version_id": candidate["id"],
                },
            )
        finally:
            integration_app.state.settings = original_settings

        assert first.status_code == 201, first.text
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == first.json()["id"]

        async with session_factory() as session:
            used = await session.scalar(
                select(OrganisationQuotaPeriod.evaluation_items_used).where(
                    OrganisationQuotaPeriod.organisation_id == tenant.organisation_id
                )
            )
        assert used == 2

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

    async def test_metrics_view_exposes_correlation_trace_and_slo(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        suite = await create_frozen_suite(tenant, count=2)
        candidate = await create_agent_version(tenant, "Observed Candidate")
        created = await tenant.client.post(
            "/api/v1/evaluation-runs",
            headers={"x-correlation-id": "cid_observed"},
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
            },
        )
        assert created.status_code == 201, created.text
        async with session_factory() as session:
            existing_events = list(
                (
                    await session.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.aggregate_type == "evaluation_run",
                            OutboxEvent.aggregate_id == str(created.json()["id"]),
                        )
                    )
                ).all()
            )
            for event in existing_events:
                event.published_at = datetime.now(UTC)
                event.attempts = 1
            session.add(
                ComparisonReport(
                    id=new_sortable_id(),
                    project_id=str(created.json()["project_id"]),
                    run_id=str(created.json()["id"]),
                    candidate_agent_version_id=str(created.json()["candidate_agent_version_id"]),
                    suite_digest="o" * 64,
                    summary={
                        "pass_rate": 0.99,
                        "regression_count": 0,
                        "reproducible": True,
                    },
                    evaluator_metrics={},
                    category_metrics={},
                    regressions=[],
                    exports={},
                )
            )
            session.add(
                OutboxEvent(
                    id=new_sortable_id(),
                    event_type="evaluation_run.resumed",
                    aggregate_type="evaluation_run",
                    aggregate_id=str(created.json()["id"]),
                    payload={},
                    attempts=3,
                )
            )
            await session.commit()

        response = await tenant.client.get(
            f"/api/v1/evaluation-runs/{created.json()['id']}/metrics"
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["correlation"]["correlation_id"] == "cid_observed"
        assert body["correlation"]["trace_id"] == created.json()["trace_id"]
        assert body["trace_links"]["recovery"].endswith("/recovery")
        assert body["queue"]["outbox_published"] is False
        assert body["queue"]["outbox_event_count"] == 2
        assert body["queue"]["outbox_pending_count"] == 1
        assert body["queue"]["outbox_published_count"] == 1
        assert body["queue"]["outbox_attempts"] == 4
        assert body["quality"]["pass_rate"] == 0.99
        assert body["slo"]["status"] == "healthy"
        assert body["runbook"]["path"] == "docs/operations/INCIDENT_RUNBOOK.md"

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

    async def test_cannot_read_another_tenants_metrics_view(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        suite = await create_frozen_suite(other_tenant)
        candidate = await create_agent_version(other_tenant, "Observed Theirs")
        created = await other_tenant.client.post(
            "/api/v1/evaluation-runs",
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
            },
        )
        assert created.status_code == 201, created.text

        response = await tenant.client.get(
            f"/api/v1/evaluation-runs/{created.json()['id']}/metrics"
        )

        assert response.status_code == 403
