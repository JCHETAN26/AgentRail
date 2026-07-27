"""Evaluator comparison API tests."""

from __future__ import annotations

import pytest
from api_test_support import Tenant
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.evaluators import (
    ComparisonReport,
    EvaluationResult,
    EvaluatorKind,
    EvaluatorResultState,
)
from agentrail_core.execution import RunItem
from agentrail_core.ids import new_sortable_id
from services.api.tests.test_execution_api import create_agent_version, create_frozen_suite

pytestmark = pytest.mark.integration


async def create_run(tenant: Tenant) -> dict[str, object]:
    suite = await create_frozen_suite(tenant, count=1)
    candidate = await create_agent_version(tenant, "Comparison Candidate")
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
    session_factory: async_sessionmaker[AsyncSession], run: dict[str, object]
) -> str:
    async with session_factory() as session:
        item = await session.scalar(select(RunItem).where(RunItem.run_id == run["id"]))
        assert item is not None
        result = EvaluationResult(
            id=new_sortable_id(),
            run_id=str(run["id"]),
            run_item_id=item.id,
            evaluator_version_id=None,
            evaluator_slug="task_success",
            evaluator_kind=EvaluatorKind.PROGRAMMATIC,
            item_index=item.item_index,
            partition=item.partition,
            category="correctness",
            state=EvaluatorResultState.PASSED,
            score=1.0,
            threshold=1.0,
            details={"reason": "recorded_result"},
        )
        report = ComparisonReport(
            id=new_sortable_id(),
            project_id=str(run["project_id"]),
            run_id=str(run["id"]),
            baseline_agent_version_id=None,
            candidate_agent_version_id=str(run["candidate_agent_version_id"]),
            suite_digest="0" * 64,
            summary={
                "item_count": 1,
                "result_count": 1,
                "pass_rate": 1.0,
                "errors_in_denominator": True,
                "reproducible": True,
            },
            evaluator_metrics={"task_success": {"total": 1, "passed": 1, "pass_rate": 1.0}},
            category_metrics={"correctness": {"total": 1, "passed": 1, "pass_rate": 1.0}},
            regressions=[],
            exports={"json": f"agentrail://evaluation-runs/{run['id']}/comparison"},
        )
        session.add_all([result, report])
        await session.commit()
        return report.id


class TestEvaluatorApi:
    async def test_reads_comparison_and_filters_results(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        report_id = await attach_comparison(session_factory, run)

        comparison = await tenant.client.get(f"/api/v1/evaluation-runs/{run['id']}/comparison")
        results = await tenant.client.get(
            f"/api/v1/evaluation-runs/{run['id']}/evaluator-results",
            params={"evaluator_slug": "task_success"},
        )

        assert comparison.status_code == 200
        assert comparison.json()["id"] == report_id
        assert comparison.json()["summary"]["reproducible"] is True
        assert results.status_code == 200
        assert results.json()["items"][0]["evaluator_slug"] == "task_success"
        assert results.json()["items"][0]["score"] == 1.0

    async def test_cannot_read_another_tenants_comparison(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run = await create_run(other_tenant)
        await attach_comparison(session_factory, run)

        comparison = await tenant.client.get(f"/api/v1/evaluation-runs/{run['id']}/comparison")
        results = await tenant.client.get(f"/api/v1/evaluation-runs/{run['id']}/evaluator-results")

        assert comparison.status_code == 403
        assert results.status_code == 403
