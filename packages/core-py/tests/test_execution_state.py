"""Execution state-machine invariants."""

from __future__ import annotations

import pytest

from agentrail_core.execution import (
    EvaluationRunState,
    IllegalExecutionTransitionError,
    RunItemState,
    assert_item_transition,
    assert_run_transition,
    is_terminal_item,
    is_terminal_run,
)


def test_run_state_machine_reaches_terminal_states_without_reopening() -> None:
    assert_run_transition(EvaluationRunState.CREATED, EvaluationRunState.VALIDATING)
    assert_run_transition(EvaluationRunState.VALIDATING, EvaluationRunState.QUEUING)
    assert_run_transition(EvaluationRunState.QUEUING, EvaluationRunState.RUNNING)
    assert_run_transition(EvaluationRunState.RUNNING, EvaluationRunState.AGGREGATING)
    assert_run_transition(EvaluationRunState.AGGREGATING, EvaluationRunState.PASSED)

    assert is_terminal_run(EvaluationRunState.PASSED)
    with pytest.raises(IllegalExecutionTransitionError):
        assert_run_transition(EvaluationRunState.PASSED, EvaluationRunState.RUNNING)


def test_run_item_retry_path_is_explicit() -> None:
    assert_item_transition(RunItemState.PENDING, RunItemState.LEASED)
    assert_item_transition(RunItemState.LEASED, RunItemState.EXECUTING)
    assert_item_transition(RunItemState.EXECUTING, RunItemState.FAILED_RETRYABLE)
    assert_item_transition(RunItemState.FAILED_RETRYABLE, RunItemState.PENDING)
    assert_item_transition(RunItemState.FAILED_RETRYABLE, RunItemState.FAILED_TERMINAL)

    assert is_terminal_item(RunItemState.FAILED_TERMINAL)
    with pytest.raises(IllegalExecutionTransitionError):
        assert_item_transition(RunItemState.FAILED_TERMINAL, RunItemState.PENDING)
