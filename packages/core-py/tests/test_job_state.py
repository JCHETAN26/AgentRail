from __future__ import annotations

import itertools

import pytest

from agentrail_core.jobs.state import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    IllegalStateTransitionError,
    JobState,
    assert_transition,
    can_transition,
    is_terminal,
)

LEGAL_TRANSITIONS = [
    (JobState.PENDING, JobState.RUNNING),
    (JobState.PENDING, JobState.FAILED),
    (JobState.RUNNING, JobState.COMPLETED),
    (JobState.RUNNING, JobState.FAILED),
]

ALL_PAIRS = list(itertools.product(JobState, JobState))
ILLEGAL_TRANSITIONS = [pair for pair in ALL_PAIRS if pair not in LEGAL_TRANSITIONS]


class TestJobStateMachine:
    @pytest.mark.parametrize(("current", "requested"), LEGAL_TRANSITIONS)
    def test_declared_transitions_are_allowed(self, current: JobState, requested: JobState) -> None:
        assert can_transition(current, requested) is True
        assert_transition(current, requested)

    @pytest.mark.parametrize(("current", "requested"), ILLEGAL_TRANSITIONS)
    def test_every_other_transition_is_rejected(
        self, current: JobState, requested: JobState
    ) -> None:
        assert can_transition(current, requested) is False
        with pytest.raises(IllegalStateTransitionError):
            assert_transition(current, requested)

    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
    def test_terminal_states_have_no_outgoing_transitions(self, state: JobState) -> None:
        """A late duplicate event can never reopen a finished job."""
        assert ALLOWED_TRANSITIONS[state] == frozenset()
        assert all(not can_transition(state, target) for target in JobState)

    @pytest.mark.parametrize("state", list(JobState))
    def test_no_state_transitions_to_itself(self, state: JobState) -> None:
        """Self-transitions would make duplicate delivery observable."""
        assert can_transition(state, state) is False

    def test_terminal_classification(self) -> None:
        assert is_terminal(JobState.COMPLETED) is True
        assert is_terminal(JobState.FAILED) is True
        assert is_terminal(JobState.PENDING) is False
        assert is_terminal(JobState.RUNNING) is False

    def test_every_state_has_a_transition_entry(self) -> None:
        assert set(ALLOWED_TRANSITIONS) == set(JobState)

    def test_every_state_is_reachable_from_pending(self) -> None:
        reachable = {JobState.PENDING}
        frontier = [JobState.PENDING]
        while frontier:
            for target in ALLOWED_TRANSITIONS[frontier.pop()]:
                if target not in reachable:
                    reachable.add(target)
                    frontier.append(target)

        assert reachable == set(JobState)

    def test_error_names_both_states(self) -> None:
        with pytest.raises(IllegalStateTransitionError) as excinfo:
            assert_transition(JobState.COMPLETED, JobState.RUNNING)

        assert excinfo.value.current is JobState.COMPLETED
        assert excinfo.value.requested is JobState.RUNNING
        assert "COMPLETED" in str(excinfo.value)
