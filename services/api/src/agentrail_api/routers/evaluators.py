"""Evaluator result and comparison endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from agentrail_api.dependencies import ActorDep, SessionDep
from agentrail_api.evaluators import service
from agentrail_api.evaluators.schemas import (
    BaselineReportRef,
    ComparisonReportResponse,
    EvaluationResultListResponse,
    EvaluationResultResponse,
    MetricDeltaResponse,
)
from agentrail_api.execution.service import principal_for_run
from agentrail_core.errors import ProblemDetail
from agentrail_core.evaluators import diff_metric_tables

router = APIRouter(prefix="/api/v1", tags=["evaluators"])

RunId = Annotated[str, Path(min_length=26, max_length=26)]
EvaluatorSlugFilter = Annotated[str | None, Query(min_length=1, max_length=64)]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
    422: {"model": ProblemDetail, "description": "Validation failed."},
}


@router.get(
    "/evaluation-runs/{run_id}/comparison",
    response_model=ComparisonReportResponse,
    summary="Fetch an evaluation comparison report",
    responses=_ERRORS,
)
async def get_comparison_report(
    run_id: RunId, actor: ActorDep, session: SessionDep
) -> ComparisonReportResponse:
    principal = await principal_for_run(session, actor, run_id)
    report = await service.get_comparison_report(session, principal, run_id=run_id)
    response = ComparisonReportResponse.model_validate(report)
    baseline = await service.get_baseline_report(session, principal, report=report)
    if baseline is not None:
        response.baseline = BaselineReportRef.model_validate(baseline)
        response.evaluator_deltas = [
            MetricDeltaResponse.model_validate(delta)
            for delta in diff_metric_tables(
                candidate=report.evaluator_metrics, baseline=baseline.evaluator_metrics
            )
        ]
        response.category_deltas = [
            MetricDeltaResponse.model_validate(delta)
            for delta in diff_metric_tables(
                candidate=report.category_metrics, baseline=baseline.category_metrics
            )
        ]
    return response


@router.get(
    "/evaluation-runs/{run_id}/evaluator-results",
    response_model=EvaluationResultListResponse,
    summary="List evaluator results for a run",
    responses=_ERRORS,
)
async def list_evaluation_results(
    run_id: RunId,
    actor: ActorDep,
    session: SessionDep,
    evaluator_slug: EvaluatorSlugFilter = None,
) -> EvaluationResultListResponse:
    principal = await principal_for_run(session, actor, run_id)
    results = await service.list_evaluation_results(
        session, principal, run_id=run_id, evaluator_slug=evaluator_slug
    )
    return EvaluationResultListResponse(
        items=[EvaluationResultResponse.model_validate(result) for result in results]
    )
