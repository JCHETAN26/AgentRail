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


async def create_run(
    tenant: Tenant,
    *,
    name: str = "Comparison Candidate",
    baseline_agent_version_id: str | None = None,
    suite: dict[str, object] | None = None,
) -> dict[str, object]:
    # Suite fixtures name their dataset after their shape, so a test that needs
    # two runs in one project must build the suite once and share it. Baseline
    # and candidate belong on the same suite anyway.
    if suite is None:
        suite = await create_frozen_suite(tenant, count=1)
    candidate = await create_agent_version(tenant, name)
    body: dict[str, object] = {
        "evaluation_suite_id": suite["id"],
        "candidate_agent_version_id": candidate["id"],
    }
    if baseline_agent_version_id is not None:
        body["baseline_agent_version_id"] = baseline_agent_version_id
    response = await tenant.client.post("/api/v1/evaluation-runs", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def attach_comparison(
    session_factory: async_sessionmaker[AsyncSession],
    run: dict[str, object],
    *,
    pass_rate: float = 1.0,
    suite_digest: str = "0" * 64,
) -> str:
    async with session_factory() as session:
        item = await session.scalar(select(RunItem).where(RunItem.run_id == run["id"]))
        assert item is not None
        baseline_version = run["baseline_agent_version_id"]
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
            baseline_agent_version_id=None if baseline_version is None else str(baseline_version),
            candidate_agent_version_id=str(run["candidate_agent_version_id"]),
            suite_digest=suite_digest,
            summary={
                "item_count": 1,
                "result_count": 1,
                "pass_rate": pass_rate,
                "errors_in_denominator": True,
                "reproducible": True,
            },
            evaluator_metrics={"task_success": {"total": 1, "passed": 1, "pass_rate": pass_rate}},
            category_metrics={"correctness": {"total": 1, "passed": 1, "pass_rate": pass_rate}},
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

    async def test_comparison_reports_baseline_to_candidate_deltas(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        suite = await create_frozen_suite(tenant, count=1)
        baseline_run = await create_run(tenant, name="Comparison Baseline", suite=suite)
        await attach_comparison(session_factory, baseline_run, pass_rate=1.0)
        candidate_run = await create_run(
            tenant,
            suite=suite,
            baseline_agent_version_id=str(baseline_run["candidate_agent_version_id"]),
        )
        await attach_comparison(session_factory, candidate_run, pass_rate=0.25)

        comparison = await tenant.client.get(
            f"/api/v1/evaluation-runs/{candidate_run['id']}/comparison"
        )

        assert comparison.status_code == 200, comparison.text
        body = comparison.json()
        assert body["baseline"]["run_id"] == baseline_run["id"]
        (evaluator_delta,) = body["evaluator_deltas"]
        assert evaluator_delta["subject"] == "task_success"
        assert evaluator_delta["status"] == "regressed"
        assert evaluator_delta["baseline"]["pass_rate"] == 1.0
        assert evaluator_delta["candidate"]["pass_rate"] == 0.25
        assert evaluator_delta["delta"]["pass_rate"] == -0.75
        (category_delta,) = body["category_deltas"]
        assert category_delta["subject"] == "correctness"
        assert category_delta["delta"]["pass_rate"] == -0.75

    async def test_comparison_omits_baseline_when_suite_digest_differs(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        suite = await create_frozen_suite(tenant, count=1)
        baseline_run = await create_run(tenant, name="Comparison Baseline", suite=suite)
        await attach_comparison(session_factory, baseline_run, suite_digest="1" * 64)
        candidate_run = await create_run(
            tenant,
            suite=suite,
            baseline_agent_version_id=str(baseline_run["candidate_agent_version_id"]),
        )
        await attach_comparison(session_factory, candidate_run, suite_digest="2" * 64)

        comparison = await tenant.client.get(
            f"/api/v1/evaluation-runs/{candidate_run['id']}/comparison"
        )

        assert comparison.status_code == 200, comparison.text
        body = comparison.json()
        assert body["baseline"] is None
        assert body["evaluator_deltas"] == []
        assert body["category_deltas"] == []

    async def test_comparison_never_uses_another_tenants_baseline(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        foreign_baseline = await create_run(other_tenant, name="Foreign Baseline")
        await attach_comparison(session_factory, foreign_baseline)
        # Name the other tenant's agent version as our baseline. Reports live in
        # their project, so ours must come back with no baseline at all.
        candidate_run = await create_run(tenant)
        await attach_comparison(session_factory, candidate_run, pass_rate=0.5)
        async with session_factory() as session:
            report = await session.scalar(
                select(ComparisonReport).where(ComparisonReport.run_id == candidate_run["id"])
            )
            assert report is not None
            report.baseline_agent_version_id = str(foreign_baseline["candidate_agent_version_id"])
            await session.commit()

        comparison = await tenant.client.get(
            f"/api/v1/evaluation-runs/{candidate_run['id']}/comparison"
        )

        assert comparison.status_code == 200, comparison.text
        assert comparison.json()["baseline"] is None
        assert comparison.json()["evaluator_deltas"] == []

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
