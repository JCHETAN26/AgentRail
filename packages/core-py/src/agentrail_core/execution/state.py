"""Evaluation run and run-item state machines."""

from __future__ import annotations

from enum import StrEnum


class EvaluationRunState(StrEnum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    QUEUING = "QUEUING"
    RUNNING = "RUNNING"
    AGGREGATING = "AGGREGATING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class RunItemState(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    EXECUTING = "EXECUTING"
    EVALUATING = "EVALUATING"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    CANCELLED = "CANCELLED"


TERMINAL_RUN_STATES: frozenset[EvaluationRunState] = frozenset(
    {
        EvaluationRunState.PASSED,
        EvaluationRunState.FAILED,
        EvaluationRunState.CANCELLED,
        EvaluationRunState.ERROR,
    }
)

TERMINAL_ITEM_STATES: frozenset[RunItemState] = frozenset(
    {RunItemState.COMPLETED, RunItemState.FAILED_TERMINAL, RunItemState.CANCELLED}
)

RUN_TRANSITIONS: dict[EvaluationRunState, frozenset[EvaluationRunState]] = {
    EvaluationRunState.CREATED: frozenset(
        {EvaluationRunState.VALIDATING, EvaluationRunState.CANCELLED, EvaluationRunState.ERROR}
    ),
    EvaluationRunState.VALIDATING: frozenset(
        {EvaluationRunState.QUEUING, EvaluationRunState.CANCELLED, EvaluationRunState.ERROR}
    ),
    EvaluationRunState.QUEUING: frozenset(
        {EvaluationRunState.RUNNING, EvaluationRunState.CANCELLED, EvaluationRunState.ERROR}
    ),
    EvaluationRunState.RUNNING: frozenset(
        {
            EvaluationRunState.AGGREGATING,
            EvaluationRunState.FAILED,
            EvaluationRunState.CANCELLED,
            EvaluationRunState.ERROR,
        }
    ),
    EvaluationRunState.AGGREGATING: frozenset(
        {EvaluationRunState.PASSED, EvaluationRunState.FAILED, EvaluationRunState.ERROR}
    ),
    EvaluationRunState.PASSED: frozenset(),
    EvaluationRunState.FAILED: frozenset(),
    EvaluationRunState.CANCELLED: frozenset(),
    EvaluationRunState.ERROR: frozenset(),
}

ITEM_TRANSITIONS: dict[RunItemState, frozenset[RunItemState]] = {
    RunItemState.PENDING: frozenset({RunItemState.LEASED, RunItemState.CANCELLED}),
    RunItemState.LEASED: frozenset(
        {RunItemState.EXECUTING, RunItemState.FAILED_RETRYABLE, RunItemState.CANCELLED}
    ),
    RunItemState.EXECUTING: frozenset(
        {RunItemState.EVALUATING, RunItemState.FAILED_RETRYABLE, RunItemState.CANCELLED}
    ),
    RunItemState.EVALUATING: frozenset(
        {
            RunItemState.COMPLETED,
            RunItemState.FAILED_RETRYABLE,
            RunItemState.FAILED_TERMINAL,
            RunItemState.CANCELLED,
        }
    ),
    RunItemState.FAILED_RETRYABLE: frozenset({RunItemState.PENDING, RunItemState.FAILED_TERMINAL}),
    RunItemState.COMPLETED: frozenset(),
    RunItemState.FAILED_TERMINAL: frozenset(),
    RunItemState.CANCELLED: frozenset(),
}


class IllegalExecutionTransitionError(Exception):
    def __init__(
        self,
        current: EvaluationRunState | RunItemState,
        requested: EvaluationRunState | RunItemState,
    ) -> None:
        super().__init__(f"Cannot transition execution state from {current} to {requested}")
        self.current = current
        self.requested = requested


def can_transition_run(current: EvaluationRunState, requested: EvaluationRunState) -> bool:
    return requested in RUN_TRANSITIONS[current]


def can_transition_item(current: RunItemState, requested: RunItemState) -> bool:
    return requested in ITEM_TRANSITIONS[current]


def assert_run_transition(current: EvaluationRunState, requested: EvaluationRunState) -> None:
    if not can_transition_run(current, requested):
        raise IllegalExecutionTransitionError(current, requested)


def assert_item_transition(current: RunItemState, requested: RunItemState) -> None:
    if not can_transition_item(current, requested):
        raise IllegalExecutionTransitionError(current, requested)


def is_terminal_run(state: EvaluationRunState) -> bool:
    return state in TERMINAL_RUN_STATES


def is_terminal_item(state: RunItemState) -> bool:
    return state in TERMINAL_ITEM_STATES
