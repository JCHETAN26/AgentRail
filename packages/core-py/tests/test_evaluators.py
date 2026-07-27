"""Evaluator aggregation tests."""

from __future__ import annotations

from agentrail_core.evaluators import (
    EvaluationResult,
    EvaluatorKind,
    EvaluatorResultState,
    aggregate_results,
    default_evaluators,
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
