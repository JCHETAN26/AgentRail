"""Public contracts for evaluator results and comparison reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from agentrail_core.evaluators import EvaluatorKind, EvaluatorResultState


class EvaluationResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    run_item_id: str
    evaluator_version_id: str | None = None
    evaluator_slug: str
    evaluator_kind: EvaluatorKind
    item_index: int
    partition: str
    category: str
    state: EvaluatorResultState
    score: float
    threshold: float
    details: dict[str, Any]
    created_at: datetime


class EvaluationResultListResponse(BaseModel):
    items: list[EvaluationResultResponse]


class ComparisonReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    run_id: str
    baseline_agent_version_id: str | None = None
    candidate_agent_version_id: str
    suite_digest: str
    summary: dict[str, Any]
    evaluator_metrics: dict[str, Any]
    category_metrics: dict[str, Any]
    regressions: list[dict[str, Any]]
    exports: dict[str, Any]
    created_at: datetime
