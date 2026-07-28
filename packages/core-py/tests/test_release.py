"""Release policy parsing and the gate decision."""

from __future__ import annotations

import pytest

from agentrail_core.release import (
    GateOutcome,
    ReleasePolicy,
    ReleasePolicyError,
    RuleKind,
    evaluate_gate,
    parse_release_policy,
)

PASSING_SUMMARY = {"pass_rate": 0.96, "regression_count": 1, "reproducible": True}
EVALUATORS = {
    "task_success": {"total": 100, "passed": 96, "failed": 4, "errors": 0, "pass_rate": 0.96},
    "tool_choice": {"total": 100, "passed": 88, "failed": 12, "errors": 0, "pass_rate": 0.88},
}
CATEGORIES = {
    "diagnosis": {"total": 100, "passed": 94, "failed": 6, "errors": 0, "pass_rate": 0.94},
}


def decide(policy: ReleasePolicy, **overrides: object):
    summary = {**PASSING_SUMMARY, **overrides}
    return evaluate_gate(
        policy,
        summary=summary,
        evaluator_metrics=EVALUATORS,
        category_metrics=CATEGORIES,
    )


def test_a_candidate_that_clears_every_threshold_passes() -> None:
    decision = decide(
        ReleasePolicy(
            min_pass_rate=0.9,
            max_regressions=5,
            min_evaluator_pass_rate={"task_success": 0.9},
        )
    )

    assert decision.outcome is GateOutcome.PASSED
    assert decision.violations == ()
    assert decision.blocked is False
    assert decision.summary_line() == "All release rules satisfied."


def test_a_pass_rate_below_the_floor_blocks_and_names_both_numbers() -> None:
    decision = decide(ReleasePolicy(min_pass_rate=0.99))

    assert decision.blocked is True
    violation = decision.violations[0]
    assert violation.kind is RuleKind.MIN_PASS_RATE
    assert violation.expected == 0.99
    assert violation.actual == 0.96
    assert "96.0%" in violation.message
    assert "99.0%" in violation.message


def test_regressions_beyond_the_cap_block() -> None:
    decision = decide(ReleasePolicy(max_regressions=0))

    assert decision.blocked is True
    assert decision.violations[0].kind is RuleKind.MAX_REGRESSIONS


def test_a_per_evaluator_floor_catches_what_the_overall_rate_hides() -> None:
    """The whole point of per-evaluator floors: 96% overall looks fine while one
    evaluator quietly sits at 88%."""
    decision = decide(
        ReleasePolicy(min_pass_rate=0.9, min_evaluator_pass_rate={"tool_choice": 0.95})
    )

    assert decision.blocked is True
    assert [v.subject for v in decision.violations] == ["tool_choice"]


def test_every_rule_is_reported_not_just_the_first() -> None:
    """A reviewer wants the whole list, not whatever the loop noticed first."""
    decision = decide(
        ReleasePolicy(
            min_pass_rate=0.99,
            max_regressions=0,
            min_evaluator_pass_rate={"tool_choice": 0.95},
        )
    )

    kinds = {violation.kind for violation in decision.violations}
    assert kinds == {
        RuleKind.MIN_PASS_RATE,
        RuleKind.MAX_REGRESSIONS,
        RuleKind.MIN_EVALUATOR_PASS_RATE,
    }
    assert decision.summary_line() == "3 release rules failed."


def test_a_metric_the_policy_requires_but_the_report_lacks_blocks() -> None:
    """Otherwise deleting an evaluator silently disables the rule guarding it —
    exactly the failure a release gate exists to prevent."""
    decision = decide(ReleasePolicy(min_evaluator_pass_rate={"deleted_evaluator": 0.9}))

    assert decision.blocked is True
    assert decision.violations[0].kind is RuleKind.MISSING_METRIC
    assert "absent" in decision.violations[0].message


def test_a_run_that_does_not_claim_reproducibility_cannot_gate_a_release() -> None:
    decision = decide(ReleasePolicy(min_pass_rate=0.5), reproducible=False)

    assert decision.blocked is True
    assert decision.violations[0].kind is RuleKind.REQUIRE_REPRODUCIBLE


def test_reproducibility_can_be_waived_deliberately() -> None:
    decision = evaluate_gate(
        ReleasePolicy(min_pass_rate=0.5, require_reproducible=False),
        summary={"pass_rate": 0.96, "regression_count": 0},
        evaluator_metrics=EVALUATORS,
        category_metrics=CATEGORIES,
    )

    assert decision.outcome is GateOutcome.PASSED


def test_required_tribunal_approval_blocks_when_verdict_is_absent() -> None:
    decision = decide(ReleasePolicy(require_tribunal_approval=True))

    assert decision.blocked is True
    assert decision.violations[0].kind is RuleKind.REQUIRE_TRIBUNAL_APPROVAL
    assert "absent" in decision.violations[0].message


@pytest.mark.parametrize("outcome", ["blocked", "conditional"])
def test_required_tribunal_approval_blocks_non_approved_verdicts(outcome: str) -> None:
    decision = evaluate_gate(
        ReleasePolicy(require_tribunal_approval=True),
        summary=PASSING_SUMMARY,
        evaluator_metrics=EVALUATORS,
        category_metrics=CATEGORIES,
        tribunal={"outcome": outcome},
    )

    assert decision.blocked is True
    assert decision.violations[0].subject == "tribunal"
    assert outcome in decision.violations[0].message


def test_required_tribunal_approval_passes_with_approved_verdict() -> None:
    decision = evaluate_gate(
        ReleasePolicy(require_tribunal_approval=True),
        summary=PASSING_SUMMARY,
        evaluator_metrics=EVALUATORS,
        category_metrics=CATEGORIES,
        tribunal={"outcome": "approved"},
    )

    assert decision.outcome is GateOutcome.PASSED


def test_a_missing_pass_rate_reads_as_zero_rather_than_passing() -> None:
    decision = evaluate_gate(
        ReleasePolicy(min_pass_rate=0.5),
        summary={"reproducible": True},
        evaluator_metrics={},
        category_metrics={},
    )

    assert decision.blocked is True
    assert decision.violations[0].actual == 0.0


def test_parsing_accepts_a_complete_policy() -> None:
    policy = parse_release_policy(
        {
            "min_pass_rate": 0.9,
            "max_regressions": 3,
            "min_evaluator_pass_rate": {"task_success": 0.95},
            "min_category_pass_rate": {"diagnosis": 0.9},
            "require_reproducible": False,
            "require_tribunal_approval": True,
        }
    )

    assert policy.min_pass_rate == 0.9
    assert policy.max_regressions == 3
    assert policy.min_evaluator_pass_rate == {"task_success": 0.95}
    assert policy.min_category_pass_rate == {"diagnosis": 0.9}
    assert policy.require_reproducible is False
    assert policy.require_tribunal_approval is True


def test_a_policy_that_forbids_nothing_is_rejected() -> None:
    """It would report 'passed' for every run, reading as evidence of quality
    rather than absence of rules."""
    with pytest.raises(ReleasePolicyError) as caught:
        parse_release_policy({"require_reproducible": True})

    assert "at least one threshold" in str(caught.value)


@pytest.mark.parametrize(
    "raw",
    [
        {"min_pass_rate": 1.5},
        {"min_pass_rate": -0.1},
        {"min_pass_rate": "high"},
        {"min_pass_rate": True},
        {"max_regressions": -1},
        {"max_regressions": 1.5},
        {"min_evaluator_pass_rate": "all"},
        {"min_evaluator_pass_rate": {"task_success": 2}},
        {"min_pass_rate": 0.9, "require_reproducible": "yes"},
        {"min_pass_rate": 0.9, "require_tribunal_approval": "yes"},
    ],
)
def test_unevaluable_policies_are_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(ReleasePolicyError):
        parse_release_policy(raw)


@pytest.mark.parametrize("raw", [None, {}])
def test_an_absent_or_empty_policy_is_refused(raw: dict[str, object] | None) -> None:
    """An empty object is not a permissive default. Accepting it would persist a
    policy that reports 'passed' for every run and reads, on a pull request, as
    evidence of quality."""
    with pytest.raises(ReleasePolicyError) as caught:
        parse_release_policy(raw)

    assert "at least one threshold" in str(caught.value)


def test_a_misspelled_rule_is_refused_rather_than_ignored() -> None:
    """The cap the author believed they had written would never be enforced,
    and one valid rule alongside it would make the policy look healthy."""
    with pytest.raises(ReleasePolicyError) as caught:
        parse_release_policy({"min_pass_rate": 0.9, "max_regressons": 0})

    assert "max_regressons" in str(caught.value)


def test_several_unknown_rules_are_all_named() -> None:
    with pytest.raises(ReleasePolicyError) as caught:
        parse_release_policy({"min_pass_rate": 0.9, "nonsense": 1, "also_wrong": 2})

    assert "also_wrong" in str(caught.value)
    assert "nonsense" in str(caught.value)
