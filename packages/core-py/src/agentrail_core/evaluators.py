"""Evaluator persistence models and deterministic aggregation helpers."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentrail_core.db import Base
from agentrail_core.execution import RunItemState


class MetricDeltaStatus(StrEnum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    UNCHANGED = "unchanged"
    ADDED = "added"
    REMOVED = "removed"


class EvaluatorKind(StrEnum):
    PROGRAMMATIC = "programmatic"
    JUDGE = "judge"


class EvaluatorResultState(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"


_EVALUATOR_KINDS = ", ".join(f"'{kind.value}'" for kind in EvaluatorKind)
_RESULT_STATES = ", ".join(f"'{state.value}'" for state in EvaluatorResultState)


class EvaluatorVersion(Base):
    __tablename__ = "evaluator_versions"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_EVALUATOR_KINDS})", name="ck_evaluator_versions_kind"),
        UniqueConstraint(
            "project_id", "slug", "version", name="uq_evaluator_versions_slug_version"
        ),
        UniqueConstraint("project_id", "definition_digest", name="uq_evaluator_versions_digest"),
        Index("ix_evaluator_versions_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[EvaluatorKind] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    definition_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        CheckConstraint(f"state IN ({_RESULT_STATES})", name="ck_evaluation_results_state"),
        CheckConstraint("item_index >= 0", name="ck_evaluation_results_item_index_non_negative"),
        UniqueConstraint(
            "run_item_id", "evaluator_slug", name="uq_evaluation_results_item_evaluator"
        ),
        Index("ix_evaluation_results_run_id", "run_id"),
        Index("ix_evaluation_results_run_evaluator", "run_id", "evaluator_slug"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    run_item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("run_items.id", ondelete="CASCADE"), nullable=False
    )
    evaluator_version_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("evaluator_versions.id", ondelete="SET NULL"), nullable=True
    )
    evaluator_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluator_kind: Mapped[EvaluatorKind] = mapped_column(String(32), nullable=False)
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    partition: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[EvaluatorResultState] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(nullable=False)
    threshold: Mapped[float] = mapped_column(nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ComparisonReport(Base):
    __tablename__ = "comparison_reports"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_comparison_reports_run_id"),
        Index("ix_comparison_reports_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    baseline_agent_version_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    candidate_agent_version_id: Mapped[str] = mapped_column(String(26), nullable=False)
    suite_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    evaluator_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    category_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    regressions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    exports: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalise_evaluator(config: dict[str, Any]) -> dict[str, Any]:
    slug = str(config.get("slug") or config.get("name") or "task_success")
    return {
        "slug": slug,
        "name": str(config.get("name") or slug.replace("_", " ").title()),
        "kind": str(config.get("kind") or EvaluatorKind.PROGRAMMATIC.value),
        "category": str(config.get("category") or slug),
        "threshold": float(config.get("threshold", 1.0)),
        "judge_enabled": bool(config.get("judge_enabled", False)),
    }


def default_evaluators(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if configs:
        return [normalise_evaluator(config) for config in configs]
    return [
        {
            "slug": "task_success",
            "name": "Task Success",
            "kind": EvaluatorKind.PROGRAMMATIC.value,
            "category": "correctness",
            "threshold": 1.0,
            "judge_enabled": False,
        }
    ]


def score_run_item(
    *, item_state: RunItemState, item_result: dict[str, Any] | None, evaluator: dict[str, Any]
) -> tuple[EvaluatorResultState, float, dict[str, Any]]:
    if item_state != RunItemState.COMPLETED:
        return EvaluatorResultState.ERROR, 0.0, {"reason": "item_not_completed"}
    passed = bool((item_result or {}).get("passed", False))
    threshold = float(evaluator["threshold"])
    score = 1.0 if passed else 0.0
    state = EvaluatorResultState.PASSED if score >= threshold else EvaluatorResultState.FAILED
    return state, score, {"reason": "recorded_result", "passed": passed}


def aggregate_results(
    *, item_count: int, results: list[EvaluationResult]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    evaluator_metrics: dict[str, Any] = {}
    category_metrics: dict[str, Any] = {}
    regressions: list[dict[str, Any]] = []
    for result in results:
        metric = evaluator_metrics.setdefault(
            result.evaluator_slug,
            {"total": 0, "passed": 0, "failed": 0, "errors": 0, "score_sum": 0.0},
        )
        metric["total"] += 1
        metric["score_sum"] += result.score
        if result.state == EvaluatorResultState.PASSED:
            metric["passed"] += 1
        elif result.state == EvaluatorResultState.FAILED:
            metric["failed"] += 1
        else:
            metric["errors"] += 1
        category = category_metrics.setdefault(
            result.category,
            {"total": 0, "passed": 0, "failed": 0, "errors": 0, "score_sum": 0.0},
        )
        category["total"] += 1
        category["score_sum"] += result.score
        if result.state == EvaluatorResultState.PASSED:
            category["passed"] += 1
        elif result.state == EvaluatorResultState.FAILED:
            category["failed"] += 1
        else:
            category["errors"] += 1
        if result.state != EvaluatorResultState.PASSED:
            regressions.append(
                {
                    "run_item_id": result.run_item_id,
                    "item_index": result.item_index,
                    "partition": result.partition,
                    "evaluator_slug": result.evaluator_slug,
                    "category": result.category,
                    "state": result.state.value,
                    "score": result.score,
                }
            )
    for metrics in (evaluator_metrics, category_metrics):
        for metric in metrics.values():
            total = int(metric["total"])
            metric["pass_rate"] = float(metric["passed"]) / total if total else 0.0
            metric["mean_score"] = float(metric["score_sum"]) / total if total else 0.0
            del metric["score_sum"]
    total_passed = sum(int(metric["passed"]) for metric in evaluator_metrics.values())
    total_results = max(item_count * max(len(evaluator_metrics), 1), 1)
    summary = {
        "item_count": item_count,
        "result_count": len(results),
        "pass_rate": total_passed / total_results,
        "regression_count": len(regressions),
        "errors_in_denominator": True,
        "reproducible": True,
    }
    return summary, evaluator_metrics, category_metrics, regressions


#: Metric fields carried through a baseline-to-candidate diff, in display order.
COMPARABLE_METRIC_FIELDS = ("pass_rate", "mean_score", "total", "passed", "failed", "errors")

#: Float noise below this magnitude is not a real change.
_DELTA_EPSILON = 1e-9

#: Fields that decide whether a subject improved or regressed, in priority order.
_STATUS_FIELDS = ("pass_rate", "mean_score")


def _comparable_values(metric: Any) -> dict[str, float]:
    """Extract the finite numeric fields of one aggregated metric entry."""
    if not isinstance(metric, dict):
        return {}
    values: dict[str, float] = {}
    for field in COMPARABLE_METRIC_FIELDS:
        raw = metric.get(field)
        # bool is an int subclass, and a flag is not a measurement.
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        number = float(raw)
        if math.isfinite(number):
            values[field] = number
    return values


def _delta_status(delta: dict[str, float]) -> MetricDeltaStatus:
    for field in _STATUS_FIELDS:
        change = delta.get(field)
        if change is None or abs(change) <= _DELTA_EPSILON:
            continue
        return MetricDeltaStatus.IMPROVED if change > 0 else MetricDeltaStatus.REGRESSED
    return MetricDeltaStatus.UNCHANGED


def diff_metric_tables(
    *, candidate: dict[str, Any], baseline: dict[str, Any]
) -> list[dict[str, Any]]:
    """Diff two aggregated metric tables subject by subject.

    Both arguments are ``{subject: {field: value}}`` tables as produced by
    :func:`aggregate_results`. Subjects present on only one side are reported as
    added or removed with no delta, because there is nothing to subtract.
    """
    deltas: list[dict[str, Any]] = []
    for subject in sorted(set(candidate) | set(baseline)):
        candidate_values = _comparable_values(candidate.get(subject))
        baseline_values = _comparable_values(baseline.get(subject))
        if subject not in baseline:
            status = MetricDeltaStatus.ADDED
            delta: dict[str, float] = {}
        elif subject not in candidate:
            status = MetricDeltaStatus.REMOVED
            delta = {}
        else:
            delta = {
                field: value - baseline_values[field]
                for field, value in candidate_values.items()
                if field in baseline_values
            }
            status = _delta_status(delta)
        deltas.append(
            {
                "subject": subject,
                "status": status.value,
                "candidate": candidate_values,
                "baseline": baseline_values,
                "delta": delta,
            }
        )
    return deltas
