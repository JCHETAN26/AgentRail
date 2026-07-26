"""The job state machine.

Pure domain logic: no SQLAlchemy, no FastAPI, no Redis. Phase 0 models a single
no-op job kind; Phase 5 replaces this with the full evaluation-run and run-item
machines, and it will reuse the same transition-guard shape.

Invariants enforced here:

* only declared transitions are legal;
* terminal states never transition, including to themselves — this is what makes
  duplicate queue delivery and delayed events safe.
"""

from __future__ import annotations

from enum import StrEnum


class JobState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


TERMINAL_STATES: frozenset[JobState] = frozenset({JobState.COMPLETED, JobState.FAILED})

ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.PENDING: frozenset({JobState.RUNNING, JobState.FAILED}),
    JobState.RUNNING: frozenset({JobState.COMPLETED, JobState.FAILED}),
    JobState.COMPLETED: frozenset(),
    JobState.FAILED: frozenset(),
}


class IllegalStateTransitionError(Exception):
    """Raised when a caller attempts a transition the machine does not allow."""

    def __init__(self, current: JobState, requested: JobState) -> None:
        super().__init__(f"Cannot transition job from {current} to {requested}")
        self.current = current
        self.requested = requested


def is_terminal(state: JobState) -> bool:
    return state in TERMINAL_STATES


def can_transition(current: JobState, requested: JobState) -> bool:
    return requested in ALLOWED_TRANSITIONS[current]


def assert_transition(current: JobState, requested: JobState) -> None:
    """Raise :class:`IllegalStateTransitionError` unless the transition is legal."""
    if not can_transition(current, requested):
        raise IllegalStateTransitionError(current, requested)
