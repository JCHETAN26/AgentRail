"""Fault profile parsing and deterministic selection."""

from __future__ import annotations

import pytest

from agentrail_core.faults import (
    FaultFamily,
    FaultKind,
    FaultProfile,
    FaultProfileError,
    parse_fault_profiles,
    plan_fault,
)


def test_every_declared_fault_family_from_the_build_plan_is_covered() -> None:
    kinds = {kind.value for kind in FaultKind}

    model = {kind for kind in kinds if kind.startswith("model.")}
    tool = {kind for kind in kinds if kind.startswith("tool.")}
    platform = {kind for kind in kinds if kind.startswith("platform.")}

    # BUILDPLAN section 15 lists 8 model, 7 tool and 8 platform faults.
    assert len(model) == 8
    assert len(tool) == 7
    assert len(platform) == 8


def test_planning_is_deterministic_for_the_same_item_and_attempt() -> None:
    profiles = parse_fault_profiles([{"kind": "tool.timeout", "every_n": 3}])

    first = plan_fault(profiles, item_index=6, attempt=1)
    second = plan_fault(profiles, item_index=6, attempt=1)

    assert first == second
    assert first is not None
    assert first.kind == FaultKind.TOOL_TIMEOUT
    assert first.family == FaultFamily.TOOL


def test_every_n_selects_only_multiples() -> None:
    profiles = parse_fault_profiles([{"kind": "tool.http_500", "every_n": 3}])

    selected = [i for i in range(10) if plan_fault(profiles, item_index=i, attempt=1) is not None]

    assert selected == [0, 3, 6, 9]


def test_explicit_item_indexes_take_precedence_over_every_n() -> None:
    profiles = parse_fault_profiles(
        [{"kind": "tool.http_500", "every_n": 3, "item_indexes": [1, 4]}]
    )

    selected = [i for i in range(10) if plan_fault(profiles, item_index=i, attempt=1) is not None]

    assert selected == [1, 4]


def test_attempt_scoping_lets_a_retry_succeed() -> None:
    """The recovery case: fail attempt 1, run clean on attempt 2."""
    profiles = parse_fault_profiles([{"kind": "tool.timeout", "attempts": [1]}])

    assert plan_fault(profiles, item_index=0, attempt=1) is not None
    assert plan_fault(profiles, item_index=0, attempt=2) is None


def test_first_matching_profile_wins_in_declaration_order() -> None:
    profiles = parse_fault_profiles(
        [{"kind": "tool.timeout"}, {"kind": "model.refusal"}],
    )

    chosen = plan_fault(profiles, item_index=0, attempt=1)

    assert chosen is not None
    assert chosen.kind == FaultKind.TOOL_TIMEOUT


def test_transient_faults_retry_and_reasoning_failures_do_not() -> None:
    """A refusal reproduces identically on a second attempt; a timeout may not."""
    transient = FaultProfile(kind=FaultKind.TOOL_TIMEOUT)
    reasoning = FaultProfile(kind=FaultKind.MODEL_REFUSAL)

    assert transient.retryable is True
    assert reasoning.retryable is False


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ({"kind": "nonsense.fault"}, "unknown fault kind"),
        ({"kind": "tool.timeout", "every_n": 0}, "every_n"),
        ({"kind": "tool.timeout", "every_n": True}, "every_n"),
        ({"kind": "tool.timeout", "item_indexes": [-1]}, "item_indexes"),
        ({"kind": "tool.timeout", "item_indexes": "all"}, "item_indexes"),
        ({"kind": "tool.timeout", "attempts": [0]}, "attempts"),
        ({"kind": "tool.timeout", "detail": "nope"}, "detail"),
    ],
)
def test_unexecutable_profiles_are_rejected_with_their_index(
    entry: dict[str, object], reason: str
) -> None:
    """Silently ignoring these is worse than refusing them — a profile that
    never fires is indistinguishable from one that found no faults."""
    with pytest.raises(FaultProfileError) as caught:
        parse_fault_profiles([{"kind": "tool.timeout"}, entry])

    assert caught.value.index == 1
    assert reason in str(caught.value)


def test_absent_profiles_produce_no_faults() -> None:
    assert parse_fault_profiles(None) == ()
    assert parse_fault_profiles([]) == ()
    assert plan_fault((), item_index=0, attempt=1) is None
