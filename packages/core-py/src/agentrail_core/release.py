"""Release policies and the gate decision.

A release policy is the answer to "what would make me refuse to ship this?",
written down before the run rather than argued about afterwards. The gate reads
a comparison report and returns a verdict plus the specific reasons for it —
those reasons become the annotations on a pull request, so they have to name a
number, not a mood.

``evaluate_gate`` is pure. It takes the report's already-computed metrics and
returns a decision; it does not touch a database, a clock or a network. The
GitHub integration is a delivery channel for this decision, never its source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentrail_core.db import Base


class GateOutcome(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"


class RuleKind(StrEnum):
    """The rule that was violated. Stable — these appear in PR annotations.

    The ``noqa`` markers below silence a scanner heuristic that reads "pass" in
    "pass_rate" as a credential. The term is the domain's, and matches the
    metric key the comparison report has emitted since Phase 7.
    """

    MIN_PASS_RATE = "min_pass_rate"  # noqa: S105
    MAX_REGRESSIONS = "max_regressions"
    MIN_EVALUATOR_PASS_RATE = "min_evaluator_pass_rate"  # noqa: S105
    MIN_CATEGORY_PASS_RATE = "min_category_pass_rate"  # noqa: S105
    REQUIRE_REPRODUCIBLE = "require_reproducible"
    #: Not a threshold: a metric the policy names is absent from the report.
    MISSING_METRIC = "missing_metric"


class ReleasePolicyError(ValueError):
    """A policy that cannot be evaluated."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"release_policy: {reason}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class GateViolation:
    """One reason the gate said no."""

    kind: RuleKind
    subject: str
    expected: float
    actual: float
    message: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "subject": self.subject,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class GateDecision:
    outcome: GateOutcome
    violations: tuple[GateViolation, ...]

    @property
    def blocked(self) -> bool:
        return self.outcome is GateOutcome.BLOCKED

    def summary_line(self) -> str:
        """One sentence, suitable for a Check Run title."""
        if not self.violations:
            return "All release rules satisfied."
        if len(self.violations) == 1:
            return self.violations[0].message
        return f"{len(self.violations)} release rules failed."

    def as_payload(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "violation_count": len(self.violations),
            "violations": [violation.as_payload() for violation in self.violations],
            "summary": self.summary_line(),
        }


@dataclass(frozen=True, slots=True)
class ReleasePolicy:
    """Thresholds a candidate must clear before it may ship."""

    #: Overall pass rate floor across every evaluator.
    min_pass_rate: float | None = None
    #: Absolute cap on regressions. Zero means none are tolerated.
    max_regressions: int | None = None
    #: Per-evaluator floors, keyed by evaluator slug.
    min_evaluator_pass_rate: dict[str, float] = field(default_factory=dict)
    #: Per-category floors, keyed by category.
    min_category_pass_rate: dict[str, float] = field(default_factory=dict)
    #: Refuse to gate on a run that does not claim reproducibility. On by
    #: default: a threshold applied to a number that will not reproduce is
    #: theatre, and worse, it is theatre that looks like evidence.
    require_reproducible: bool = True

    def as_payload(self) -> dict[str, Any]:
        return {
            "min_pass_rate": self.min_pass_rate,
            "max_regressions": self.max_regressions,
            "min_evaluator_pass_rate": dict(self.min_evaluator_pass_rate),
            "min_category_pass_rate": dict(self.min_category_pass_rate),
            "require_reproducible": self.require_reproducible,
        }


def evaluate_gate(
    policy: ReleasePolicy,
    *,
    summary: dict[str, Any],
    evaluator_metrics: dict[str, Any],
    category_metrics: dict[str, Any],
) -> GateDecision:
    """Judge one comparison report against one policy.

    Every rule is evaluated, not short-circuited at the first failure: a
    reviewer wants the whole list of what is wrong, not the first thing the
    loop happened to notice.
    """
    violations: list[GateViolation] = []

    if policy.require_reproducible and not bool(summary.get("reproducible", False)):
        violations.append(
            GateViolation(
                kind=RuleKind.REQUIRE_REPRODUCIBLE,
                subject="run",
                expected=1.0,
                actual=0.0,
                message=(
                    "The run does not claim reproducibility, so its numbers cannot gate a release."
                ),
            )
        )

    if policy.min_pass_rate is not None:
        actual = _rate(summary.get("pass_rate"))
        if actual < policy.min_pass_rate:
            violations.append(
                GateViolation(
                    kind=RuleKind.MIN_PASS_RATE,
                    subject="overall",
                    expected=policy.min_pass_rate,
                    actual=actual,
                    message=(
                        f"Overall pass rate {actual:.1%} is below the required "
                        f"{policy.min_pass_rate:.1%}."
                    ),
                )
            )

    if policy.max_regressions is not None:
        actual_count = int(summary.get("regression_count", 0) or 0)
        if actual_count > policy.max_regressions:
            violations.append(
                GateViolation(
                    kind=RuleKind.MAX_REGRESSIONS,
                    subject="overall",
                    expected=float(policy.max_regressions),
                    actual=float(actual_count),
                    message=(
                        f"{actual_count} regressions exceed the allowed {policy.max_regressions}."
                    ),
                )
            )

    violations.extend(
        _threshold_violations(
            thresholds=policy.min_evaluator_pass_rate,
            metrics=evaluator_metrics,
            kind=RuleKind.MIN_EVALUATOR_PASS_RATE,
            noun="Evaluator",
        )
    )
    violations.extend(
        _threshold_violations(
            thresholds=policy.min_category_pass_rate,
            metrics=category_metrics,
            kind=RuleKind.MIN_CATEGORY_PASS_RATE,
            noun="Category",
        )
    )

    outcome = GateOutcome.BLOCKED if violations else GateOutcome.PASSED
    return GateDecision(outcome=outcome, violations=tuple(violations))


def _threshold_violations(
    *,
    thresholds: dict[str, float],
    metrics: dict[str, Any],
    kind: RuleKind,
    noun: str,
) -> list[GateViolation]:
    violations: list[GateViolation] = []
    for subject, floor in sorted(thresholds.items()):
        entry = metrics.get(subject)
        if not isinstance(entry, dict):
            # A metric the policy names but the report does not contain blocks.
            # Passing here would mean deleting an evaluator silently disables
            # the rule that guarded it — the failure mode a release gate exists
            # to prevent.
            violations.append(
                GateViolation(
                    kind=RuleKind.MISSING_METRIC,
                    subject=subject,
                    expected=floor,
                    actual=0.0,
                    message=(
                        f"{noun} {subject!r} is required by the release policy but absent "
                        f"from this run's report."
                    ),
                )
            )
            continue
        actual = _rate(entry.get("pass_rate"))
        if actual < floor:
            violations.append(
                GateViolation(
                    kind=kind,
                    subject=subject,
                    expected=floor,
                    actual=actual,
                    message=(
                        f"{noun} {subject!r} passed {actual:.1%}, below the required {floor:.1%}."
                    ),
                )
            )
    return violations


#: Every key a policy may contain. Anything else is a typo, and a typo in a
#: release policy is a rule that silently never fires.
_KNOWN_KEYS: frozenset[str] = frozenset(
    {
        "min_pass_rate",
        "max_regressions",
        "min_evaluator_pass_rate",
        "min_category_pass_rate",
        "require_reproducible",
    }
)


def parse_release_policy(raw: dict[str, Any] | None) -> ReleasePolicy:
    """Build an evaluable policy from stored JSON, rejecting what cannot run.

    An absent or empty definition is refused rather than treated as a permissive
    default. A policy object that forbids nothing reports "passed" for every run
    and reads, on a pull request, as evidence of quality.
    """
    if raw is None:
        raise ReleasePolicyError("a policy must contain at least one threshold")
    if not isinstance(raw, dict):
        raise ReleasePolicyError("must be an object")

    unknown = sorted(set(raw) - _KNOWN_KEYS)
    if unknown:
        # Refused rather than ignored. "max_regressons" would otherwise be
        # accepted alongside one valid rule, and the cap the author believed
        # they had written would never be enforced.
        raise ReleasePolicyError(f"unknown rule(s): {', '.join(unknown)}")

    min_pass_rate = _optional_rate(raw.get("min_pass_rate"), "min_pass_rate")
    max_regressions = raw.get("max_regressions")
    if max_regressions is not None and (
        not isinstance(max_regressions, int)
        or isinstance(max_regressions, bool)
        or max_regressions < 0
    ):
        raise ReleasePolicyError("max_regressions must be a non-negative integer")

    require_reproducible = raw.get("require_reproducible", True)
    if not isinstance(require_reproducible, bool):
        raise ReleasePolicyError("require_reproducible must be a boolean")

    policy = ReleasePolicy(
        min_pass_rate=min_pass_rate,
        max_regressions=max_regressions,
        min_evaluator_pass_rate=_rate_map(
            raw.get("min_evaluator_pass_rate"), "min_evaluator_pass_rate"
        ),
        min_category_pass_rate=_rate_map(
            raw.get("min_category_pass_rate"), "min_category_pass_rate"
        ),
        require_reproducible=require_reproducible,
    )
    if not _has_any_rule(policy):
        # A policy that forbids nothing would report "passed" for every run,
        # which reads as evidence of quality rather than absence of rules.
        raise ReleasePolicyError("a policy must contain at least one threshold")
    return policy


def _has_any_rule(policy: ReleasePolicy) -> bool:
    return any(
        (
            policy.min_pass_rate is not None,
            policy.max_regressions is not None,
            bool(policy.min_evaluator_pass_rate),
            bool(policy.min_category_pass_rate),
        )
    )


def _rate_map(value: Any, field_name: str) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ReleasePolicyError(f"{field_name} must be an object")
    parsed: dict[str, float] = {}
    for key, raw_rate in value.items():
        rate = _optional_rate(raw_rate, f"{field_name}[{key!r}]")
        if rate is None:
            raise ReleasePolicyError(f"{field_name}[{key!r}] must be a rate")
        parsed[str(key)] = rate
    return parsed


def _optional_rate(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ReleasePolicyError(f"{field_name} must be a number between 0 and 1")
    rate = float(value)
    if not 0.0 <= rate <= 1.0:
        raise ReleasePolicyError(f"{field_name} must be between 0 and 1")
    return rate


def _rate(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class ReleasePolicyRecord(Base):
    """A named set of thresholds, immutable once created.

    Immutable for the same reason agent versions are: when a gate blocks a pull
    request, "which rules was it judged against?" must have exactly one answer,
    and an editable policy would let that answer change after the fact. A change
    is a new version.
    """

    __tablename__ = "release_policies"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", "version", name="uq_release_policies_slug_version"),
        UniqueConstraint(
            "project_id", "definition_digest", name="uq_release_policies_project_digest"
        ),
        CheckConstraint("version >= 1", name="ck_release_policies_version_positive"),
        Index("ix_release_policies_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    definition: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    definition_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GateEvaluation(Base):
    """One verdict: this run, judged against this policy.

    Unique on the pair, because the decision is a pure function of them. Asking
    twice — a redelivered webhook, a retried CI step — returns the recorded
    answer rather than computing a second one that could differ if the report
    were somehow edited in between.
    """

    __tablename__ = "gate_evaluations"
    __table_args__ = (
        UniqueConstraint("run_id", "release_policy_id", name="uq_gate_evaluations_run_policy"),
        CheckConstraint("outcome IN ('passed', 'blocked')", name="ck_gate_evaluations_outcome"),
        Index("ix_gate_evaluations_project_id", "project_id"),
        Index("ix_gate_evaluations_head_sha", "head_sha"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    release_policy_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("release_policies.id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    violations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    summary: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    #: Where this verdict was reported, when it was reported anywhere. All
    #: nullable: the gate is fully usable with no GitHub involvement at all.
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    check_run: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
