"""Evaluator aggregation tests."""

from __future__ import annotations

from agentrail_core.evaluators import (
    EvaluationResult,
    EvaluatorKind,
    EvaluatorResultState,
    aggregate_results,
    default_evaluators,
    diff_metric_tables,
    score_run_item,
)
from agentrail_core.execution import RunItemState


def test_default_evaluator_scores_completed_items() -> None:
    evaluator = default_evaluators([])[0]

    passed, pass_score, pass_details = score_run_item(
        item_state=RunItemState.COMPLETED,
        item_result={"passed": True},
        evaluator=evaluator,
    )
    failed, fail_score, fail_details = score_run_item(
        item_state=RunItemState.FAILED_TERMINAL,
        item_result=None,
        evaluator=evaluator,
    )

    assert passed == EvaluatorResultState.PASSED
    assert pass_score == 1.0
    assert pass_details["reason"] == "recorded_result"
    assert failed == EvaluatorResultState.ERROR
    assert fail_score == 0.0
    assert fail_details["reason"] == "item_not_completed"


def test_aggregation_keeps_errors_in_denominator() -> None:
    results = [
        EvaluationResult(
            id="r1",
            run_id="run",
            run_item_id="item-1",
            evaluator_version_id=None,
            evaluator_slug="task_success",
            evaluator_kind=EvaluatorKind.PROGRAMMATIC,
            item_index=0,
            partition="p0",
            category="correctness",
            state=EvaluatorResultState.PASSED,
            score=1.0,
            threshold=1.0,
            details={},
        ),
        EvaluationResult(
            id="r2",
            run_id="run",
            run_item_id="item-2",
            evaluator_version_id=None,
            evaluator_slug="task_success",
            evaluator_kind=EvaluatorKind.PROGRAMMATIC,
            item_index=1,
            partition="p0",
            category="correctness",
            state=EvaluatorResultState.ERROR,
            score=0.0,
            threshold=1.0,
            details={},
        ),
    ]

    summary, evaluator_metrics, category_metrics, regressions = aggregate_results(
        item_count=2, results=results
    )

    assert summary["pass_rate"] == 0.5
    assert summary["errors_in_denominator"] is True
    assert evaluator_metrics["task_success"]["errors"] == 1
    assert category_metrics["correctness"]["pass_rate"] == 0.5
    assert regressions == [
        {
            "run_item_id": "item-2",
            "item_index": 1,
            "partition": "p0",
            "evaluator_slug": "task_success",
            "category": "correctness",
            "state": "ERROR",
            "score": 0.0,
        }
    ]


def test_metric_diff_reports_direction_per_subject() -> None:
    deltas = diff_metric_tables(
        candidate={
            "task_success": {"pass_rate": 0.75, "mean_score": 0.8, "total": 8},
            "budget": {"pass_rate": 0.5, "mean_score": 0.5, "total": 8},
            "latency": {"pass_rate": 1.0, "mean_score": 1.0, "total": 8},
            "new_check": {"pass_rate": 1.0, "mean_score": 1.0, "total": 8},
        },
        baseline={
            "task_success": {"pass_rate": 0.5, "mean_score": 0.6, "total": 8},
            "budget": {"pass_rate": 0.875, "mean_score": 0.9, "total": 8},
            "latency": {"pass_rate": 1.0, "mean_score": 1.0, "total": 4},
            "retired_check": {"pass_rate": 1.0, "mean_score": 1.0, "total": 8},
        },
    )

    by_subject = {delta["subject"]: delta for delta in deltas}
    assert [delta["subject"] for delta in deltas] == sorted(by_subject)
    assert by_subject["task_success"]["status"] == "improved"
    assert by_subject["task_success"]["delta"]["pass_rate"] == 0.25
    assert by_subject["budget"]["status"] == "regressed"
    assert by_subject["budget"]["delta"]["pass_rate"] == -0.375
    # Sample size moved but the scores did not, so this is not a regression.
    assert by_subject["latency"]["status"] == "unchanged"
    assert by_subject["latency"]["delta"]["total"] == 4.0
    assert by_subject["new_check"]["status"] == "added"
    assert by_subject["new_check"]["baseline"] == {}
    assert by_subject["retired_check"]["status"] == "removed"
    assert by_subject["retired_check"]["candidate"] == {}


def test_metric_diff_ignores_non_numeric_fields() -> None:
    (delta,) = diff_metric_tables(
        candidate={"task_success": {"pass_rate": 1.0, "reproducible": True, "label": "ok"}},
        baseline={"task_success": {"pass_rate": 1.0, "reproducible": False, "label": "ok"}},
    )

    assert delta["candidate"] == {"pass_rate": 1.0}
    assert delta["delta"] == {"pass_rate": 0.0}
    assert delta["status"] == "unchanged"


def test_metric_diff_skips_fields_missing_from_one_side() -> None:
    (delta,) = diff_metric_tables(
        candidate={"task_success": {"pass_rate": 0.5, "mean_score": 0.5}},
        baseline={"task_success": {"pass_rate": 0.5}},
    )

    assert delta["delta"] == {"pass_rate": 0.0}
    assert delta["candidate"]["mean_score"] == 0.5
