"""Policy bundle parsing and the decision itself."""

from __future__ import annotations

import pytest

from agentrail_core.policy import (
    PolicyBundle,
    PolicyBundleError,
    PolicyDecision,
    ToolRiskLevel,
    decide,
    parse_policy_bundle,
)


def test_read_only_and_low_risk_writes_are_allowed() -> None:
    bundle = parse_policy_bundle(
        {"tool_risks": {"search_logs": "READ_ONLY", "notify_oncall": "LOW_RISK_WRITE"}}
    )

    assert decide(bundle, tool="search_logs") == (PolicyDecision.ALLOW, ToolRiskLevel.READ_ONLY)
    assert decide(bundle, tool="notify_oncall") == (
        PolicyDecision.ALLOW,
        ToolRiskLevel.LOW_RISK_WRITE,
    )


def test_high_risk_writes_require_approval() -> None:
    bundle = parse_policy_bundle({"tool_risks": {"restart_service": "HIGH_RISK_WRITE"}})

    assert decide(bundle, tool="restart_service") == (
        PolicyDecision.REQUIRE_APPROVAL,
        ToolRiskLevel.HIGH_RISK_WRITE,
    )


def test_prohibited_is_denied_and_no_approval_can_rescue_it() -> None:
    """The whole reason for a level above 'needs approval'."""
    bundle = parse_policy_bundle({"tool_risks": {"drop_database": "PROHIBITED"}})

    assert decide(bundle, tool="drop_database") == (PolicyDecision.DENY, ToolRiskLevel.PROHIBITED)


def test_an_unclassified_tool_stops_for_a_human_by_default() -> None:
    """A policy engine that fails open is not a policy engine."""
    bundle = parse_policy_bundle({"tool_risks": {"search_logs": "READ_ONLY"}})

    verdict, risk = decide(bundle, tool="a_tool_nobody_classified")

    assert verdict is PolicyDecision.REQUIRE_APPROVAL
    assert risk is ToolRiskLevel.HIGH_RISK_WRITE


def test_an_empty_bundle_still_defends() -> None:
    for raw in (None, {}):
        assert decide(parse_policy_bundle(raw), tool="anything")[0] is (
            PolicyDecision.REQUIRE_APPROVAL
        )


def test_the_approval_threshold_can_be_lowered() -> None:
    bundle = parse_policy_bundle(
        {
            "tool_risks": {"notify_oncall": "LOW_RISK_WRITE", "search_logs": "READ_ONLY"},
            "require_approval_at": "LOW_RISK_WRITE",
        }
    )

    assert decide(bundle, tool="notify_oncall")[0] is PolicyDecision.REQUIRE_APPROVAL
    assert decide(bundle, tool="search_logs")[0] is PolicyDecision.ALLOW


def test_the_default_risk_can_be_relaxed_deliberately() -> None:
    bundle = parse_policy_bundle({"default_risk": "READ_ONLY"})

    assert decide(bundle, tool="unlisted")[0] is PolicyDecision.ALLOW


def test_a_bundle_that_could_never_require_approval_is_rejected() -> None:
    """Anything at PROHIBITED is denied outright, so this threshold would make
    the approval path unreachable — an inert policy, silently."""
    with pytest.raises(PolicyBundleError) as caught:
        parse_policy_bundle({"require_approval_at": "PROHIBITED"})

    assert "PROHIBITED" in str(caught.value)


@pytest.mark.parametrize(
    "raw",
    [
        {"tool_risks": "nope"},
        {"tool_risks": {"restart_service": "VERY_RISKY"}},
        {"tool_risks": {"restart_service": 3}},
        {"default_risk": "sideways"},
        {"require_approval_at": None},
    ],
)
def test_unevaluable_bundles_are_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(PolicyBundleError):
        parse_policy_bundle(raw)


def test_risk_of_is_the_single_source_of_the_level() -> None:
    bundle = PolicyBundle(tool_risks={"restart_service": ToolRiskLevel.HIGH_RISK_WRITE})

    assert bundle.risk_of("restart_service") is ToolRiskLevel.HIGH_RISK_WRITE
    assert bundle.risk_of("unknown") is bundle.default_risk
