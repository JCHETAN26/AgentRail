"""Evaluator result and comparison endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from agentrail_api.dependencies import ActorDep, SessionDep
from agentrail_api.evaluators import service
from agentrail_api.evaluators.schemas import (
    ComparisonReportResponse,
    EvaluationResultListResponse,
    EvaluationResultResponse,
)
from agentrail_api.execution.service import principal_for_run
from agentrail_core.errors import ProblemDetail

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
    return ComparisonReportResponse.model_validate(report)


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
