"""Tool risk classification and the policy decision.

The four levels are the ones named in ``BUILDPLAN.md`` section 16. They are not
the same vocabulary as ``agentrail_cloudops_sandbox.cloudops.RiskLevel``, and
deliberately so: the sandbox's levels describe what a tool *is*, as a property
of that synthetic service, while these describe what the platform *does* about
it. Core cannot import a service anyway.

``decide`` is pure and total. Like ``authorize``, it is the single place the
question is answered, so a caller cannot accidentally reach a different verdict
by asking differently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ToolRiskLevel(StrEnum):
    READ_ONLY = "READ_ONLY"
    LOW_RISK_WRITE = "LOW_RISK_WRITE"
    HIGH_RISK_WRITE = "HIGH_RISK_WRITE"
    PROHIBITED = "PROHIBITED"


#: Severity order. Used to compare a tool's level against the approval
#: threshold, so raising the threshold is one edit rather than a rule rewrite.
_SEVERITY: dict[ToolRiskLevel, int] = {
    ToolRiskLevel.READ_ONLY: 0,
    ToolRiskLevel.LOW_RISK_WRITE: 1,
    ToolRiskLevel.HIGH_RISK_WRITE: 2,
    ToolRiskLevel.PROHIBITED: 3,
}


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class PolicyBundleError(ValueError):
    """An agent version carried a policy bundle that cannot be evaluated."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"policy_bundle: {reason}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PolicyBundle:
    """How one agent version is allowed to use its tools."""

    tool_risks: dict[str, ToolRiskLevel] = field(default_factory=dict)
    #: What an *unlisted* tool is treated as. Defaults to high risk, so a tool
    #: nobody classified stops for a human rather than sailing through. A policy
    #: engine that fails open is not a policy engine.
    default_risk: ToolRiskLevel = ToolRiskLevel.HIGH_RISK_WRITE
    #: The level at or above which a human must approve. Below it, allow.
    require_approval_at: ToolRiskLevel = ToolRiskLevel.HIGH_RISK_WRITE

    def risk_of(self, tool: str) -> ToolRiskLevel:
        return self.tool_risks.get(tool, self.default_risk)


def decide(bundle: PolicyBundle, *, tool: str) -> tuple[PolicyDecision, ToolRiskLevel]:
    """Return the verdict for one tool and the risk level it was judged at.

    The level travels with the verdict because every caller that acts on the
    decision also has to record *why* — in an approval request, an audit event
    or a trajectory step — and recomputing it separately invites the two to
    drift apart.
    """
    risk = bundle.risk_of(tool)
    if risk == ToolRiskLevel.PROHIBITED:
        # No human can approve their way past this one. That is the point of
        # having a level above "needs approval".
        return PolicyDecision.DENY, risk
    if _SEVERITY[risk] >= _SEVERITY[bundle.require_approval_at]:
        return PolicyDecision.REQUIRE_APPROVAL, risk
    return PolicyDecision.ALLOW, risk


def parse_policy_bundle(raw: dict[str, Any] | None) -> PolicyBundle:
    """Build an executable bundle from an agent version's stored JSON.

    Rejected at the boundary rather than at run time: a bundle that only fails
    when the worker reads it would park a leased item behind an error nobody
    asked for.
    """
    if not raw:
        return PolicyBundle()
    if not isinstance(raw, dict):
        raise PolicyBundleError("must be an object")

    raw_risks = raw.get("tool_risks", {})
    if not isinstance(raw_risks, dict):
        raise PolicyBundleError("tool_risks must be an object")
    tool_risks: dict[str, ToolRiskLevel] = {}
    for tool, raw_level in raw_risks.items():
        tool_risks[str(tool)] = _level(raw_level, f"tool_risks[{tool!r}]")

    default_risk = (
        _level(raw["default_risk"], "default_risk")
        if "default_risk" in raw
        else ToolRiskLevel.HIGH_RISK_WRITE
    )
    require_approval_at = (
        _level(raw["require_approval_at"], "require_approval_at")
        if "require_approval_at" in raw
        else ToolRiskLevel.HIGH_RISK_WRITE
    )
    if require_approval_at == ToolRiskLevel.PROHIBITED:
        # Nothing would ever require approval, because anything reaching that
        # level is denied outright. Almost certainly a mistake, and a silently
        # inert policy is worse than a rejected one.
        raise PolicyBundleError("require_approval_at cannot be PROHIBITED")

    return PolicyBundle(
        tool_risks=tool_risks,
        default_risk=default_risk,
        require_approval_at=require_approval_at,
    )


def _level(value: Any, field_name: str) -> ToolRiskLevel:
    if not isinstance(value, str):
        raise PolicyBundleError(f"{field_name} must be a risk level")
    try:
        return ToolRiskLevel(value)
    except ValueError:
        raise PolicyBundleError(f"{field_name} is not a known risk level: {value!r}") from None
