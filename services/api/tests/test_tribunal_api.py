"""Multi-agent Tribunal API tests."""

from __future__ import annotations

import pytest
from api_test_support import Tenant
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.evaluators import ComparisonReport
from agentrail_core.ids import new_sortable_id
from agentrail_core.tribunal import TribunalSession
from services.api.tests.test_execution_api import create_agent_version, create_frozen_suite

pytestmark = pytest.mark.integration


async def create_run(tenant: Tenant, *, count: int = 16) -> dict[str, object]:
    suite = await create_frozen_suite(tenant, count=count)
    candidate = await create_agent_version(tenant, "Tribunal Candidate")
    response = await tenant.client.post(
        "/api/v1/evaluation-runs",
        json={
            "evaluation_suite_id": suite["id"],
            "candidate_agent_version_id": candidate["id"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def attach_comparison(
    session_factory: async_sessionmaker[AsyncSession],
    run: dict[str, object],
    *,
    reproducible: bool = True,
    pass_rate: float = 1.0,
) -> None:
    async with session_factory() as session:
        report = ComparisonReport(
            id=new_sortable_id(),
            project_id=str(run["project_id"]),
            run_id=str(run["id"]),
            baseline_agent_version_id=None,
            candidate_agent_version_id=str(run["candidate_agent_version_id"]),
            suite_digest="0" * 64,
            summary={
                "item_count": int(run["item_count"]),
                "result_count": int(run["item_count"]),
                "pass_rate": pass_rate,
                "regression_count": 0,
                "errors_in_denominator": True,
                "reproducible": reproducible,
            },
            evaluator_metrics={"task_success": {"total": 16, "passed": 16, "pass_rate": pass_rate}},
            category_metrics={"correctness": {"total": 16, "passed": 16, "pass_rate": pass_rate}},
            regressions=[],
            exports={"json": f"agentrail://evaluation-runs/{run['id']}/comparison"},
        )
        session.add(report)
        await session.commit()


class TestTribunalApi:
    async def test_creates_and_fetches_a_reproducible_tribunal_session(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_comparison(session_factory, run)

        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        replay = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        fetched = await tenant.client.get(f"/api/v1/evaluation-runs/{run['id']}/tribunal")

        assert created.status_code == 201, created.text
        assert replay.status_code == 200, replay.text
        assert fetched.status_code == 200, fetched.text
        assert replay.json()["id"] == created.json()["id"] == fetched.json()["id"]
        assert created.json()["outcome"] == "approved"
        assert created.json()["summary"]["agent_count"] == 6
        assert len(created.json()["findings"]) >= 5
        assert created.json()["verdict"]["outcome"] == "approved"
        assert [entry["sequence"] for entry in created.json()["blackboard"]] == list(
            range(1, len(created.json()["blackboard"]) + 1)
        )

    async def test_auditor_blocker_overrides_defender_approval(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_comparison(session_factory, run, reproducible=False)

        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")

        assert created.status_code == 201, created.text
        assert created.json()["outcome"] == "blocked"
        assert created.json()["verdict"]["dissent"]["defender_supported_approval"] is True
        assert created.json()["verdict"]["dissent"]["auditor_blockers"] == 1

    async def test_missing_comparison_blocks_approval(self, tenant: Tenant) -> None:
        run = await create_run(tenant)

        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")

        assert created.status_code == 201, created.text
        assert created.json()["outcome"] == "blocked"
        assert created.json()["summary"]["blocker_count"] == 1

    async def test_cannot_read_or_create_another_tenants_tribunal(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run = await create_run(other_tenant)
        await attach_comparison(session_factory, run)

        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        fetched = await tenant.client.get(f"/api/v1/evaluation-runs/{run['id']}/tribunal")

        assert created.status_code == 403
        assert fetched.status_code == 403

        async with session_factory() as session:
            tribunal = await session.scalar(
                select(TribunalSession).where(TribunalSession.run_id == run["id"])
            )
        assert tribunal is None
