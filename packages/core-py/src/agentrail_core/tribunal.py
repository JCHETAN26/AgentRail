"""Deterministic multi-agent Tribunal decisions and persistence models."""

from __future__ import annotations

from dataclasses import dataclass
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from agentrail_core.db import Base


class TribunalAgentRole(StrEnum):
    PROSECUTOR = "prosecutor"
    DEFENDER = "defender"
    AUDITOR = "auditor"
    ECONOMIST = "economist"
    HISTORIAN = "historian"
    JUDGE = "judge"


class TribunalRound(StrEnum):
    EVIDENCE = "evidence"
    DEBATE = "debate"
    VERDICT = "verdict"


class TribunalFindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKER = "blocker"


class TribunalArgumentStance(StrEnum):
    SUPPORTS_APPROVAL = "supports_approval"
    SUPPORTS_CONDITIONAL = "supports_conditional"
    SUPPORTS_BLOCK = "supports_block"


class TribunalVerdictOutcome(StrEnum):
    APPROVED = "approved"
    CONDITIONAL = "conditional"
    BLOCKED = "blocked"


class TribunalSessionState(StrEnum):
    COMPLETED = "completed"


_AGENT_ROLES = ", ".join(f"'{role.value}'" for role in TribunalAgentRole)
_ROUNDS = ", ".join(f"'{round_.value}'" for round_ in TribunalRound)
_FINDING_SEVERITIES = ", ".join(f"'{severity.value}'" for severity in TribunalFindingSeverity)
_ARGUMENT_STANCES = ", ".join(f"'{stance.value}'" for stance in TribunalArgumentStance)
_VERDICT_OUTCOMES = ", ".join(f"'{outcome.value}'" for outcome in TribunalVerdictOutcome)
_SESSION_STATES = ", ".join(f"'{state.value}'" for state in TribunalSessionState)


@dataclass(frozen=True, slots=True)
class TribunalFindingDraft:
    agent_role: TribunalAgentRole
    severity: TribunalFindingSeverity
    subject: str
    message: str
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TribunalArgumentDraft:
    round: TribunalRound
    agent_role: TribunalAgentRole
    stance: TribunalArgumentStance
    message: str
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TribunalDraft:
    outcome: TribunalVerdictOutcome
    primary_reason: str
    findings: tuple[TribunalFindingDraft, ...]
    arguments: tuple[TribunalArgumentDraft, ...]
    dissent: dict[str, Any]
    evidence: dict[str, Any]
    summary: dict[str, Any]


def decide_tribunal(*, run: dict[str, Any], comparison: dict[str, Any] | None) -> TribunalDraft:
    """Run the deterministic Tribunal over run/comparison evidence.

    This is intentionally rule-based for the first slice: it gives the platform
    the multi-agent shape, persistence and safety invariants without requiring
    model credentials or making CI non-deterministic.
    """
    summary = comparison.get("summary", {}) if comparison else {}
    pass_rate = _number(summary.get("pass_rate"))
    regression_count = int(summary.get("regression_count") or 0)
    reproducible = bool(summary.get("reproducible", False))
    failed_count = int(run.get("failed_count") or 0)
    item_count = int(run.get("item_count") or 0)

    findings: list[TribunalFindingDraft] = [
        TribunalFindingDraft(
            agent_role=TribunalAgentRole.HISTORIAN,
            severity=TribunalFindingSeverity.INFO,
            subject="run",
            message=f"Run {run['id']} covers {item_count} evaluation items.",
            evidence={"run_id": run["id"], "item_count": item_count},
        )
    ]
    arguments: list[TribunalArgumentDraft] = []

    if failed_count > 0 or pass_rate < 1.0 or regression_count > 0:
        findings.append(
            TribunalFindingDraft(
                agent_role=TribunalAgentRole.PROSECUTOR,
                severity=TribunalFindingSeverity.WARNING,
                subject="quality",
                message="The candidate has failures, regressions or an incomplete pass rate.",
                evidence={
                    "failed_count": failed_count,
                    "pass_rate": pass_rate,
                    "regression_count": regression_count,
                },
            )
        )
        arguments.append(
            TribunalArgumentDraft(
                round=TribunalRound.DEBATE,
                agent_role=TribunalAgentRole.PROSECUTOR,
                stance=TribunalArgumentStance.SUPPORTS_CONDITIONAL,
                message="Quality evidence requires human review before approval.",
                evidence={"subject": "quality"},
            )
        )
    else:
        findings.append(
            TribunalFindingDraft(
                agent_role=TribunalAgentRole.PROSECUTOR,
                severity=TribunalFindingSeverity.INFO,
                subject="quality",
                message="No quality regressions were found by deterministic evidence checks.",
                evidence={"pass_rate": pass_rate, "regression_count": regression_count},
            )
        )

    if pass_rate >= 1.0 and failed_count == 0 and regression_count == 0:
        findings.append(
            TribunalFindingDraft(
                agent_role=TribunalAgentRole.DEFENDER,
                severity=TribunalFindingSeverity.INFO,
                subject="defense",
                message="The defense found no deterministic quality evidence against approval.",
                evidence={"pass_rate": pass_rate, "failed_count": failed_count},
            )
        )
        arguments.append(
            TribunalArgumentDraft(
                round=TribunalRound.DEBATE,
                agent_role=TribunalAgentRole.DEFENDER,
                stance=TribunalArgumentStance.SUPPORTS_APPROVAL,
                message="The run is clean on deterministic quality evidence.",
                evidence={"pass_rate": pass_rate, "failed_count": failed_count},
            )
        )
    else:
        findings.append(
            TribunalFindingDraft(
                agent_role=TribunalAgentRole.DEFENDER,
                severity=TribunalFindingSeverity.INFO,
                subject="defense",
                message="The defense recommends targeted review instead of automatic rejection.",
                evidence={"pass_rate": pass_rate, "failed_count": failed_count},
            )
        )
        arguments.append(
            TribunalArgumentDraft(
                round=TribunalRound.DEBATE,
                agent_role=TribunalAgentRole.DEFENDER,
                stance=TribunalArgumentStance.SUPPORTS_CONDITIONAL,
                message=(
                    "The candidate may still be acceptable after review of the flagged evidence."
                ),
                evidence={"pass_rate": pass_rate, "failed_count": failed_count},
            )
        )

    if comparison is None:
        findings.append(
            TribunalFindingDraft(
                agent_role=TribunalAgentRole.AUDITOR,
                severity=TribunalFindingSeverity.BLOCKER,
                subject="evidence",
                message="Comparison evidence is missing, so the Tribunal cannot approve the run.",
                evidence={"missing": "comparison_report"},
            )
        )
    elif not reproducible:
        findings.append(
            TribunalFindingDraft(
                agent_role=TribunalAgentRole.AUDITOR,
                severity=TribunalFindingSeverity.BLOCKER,
                subject="reproducibility",
                message="The comparison report does not claim reproducibility.",
                evidence={"reproducible": reproducible},
            )
        )
    else:
        findings.append(
            TribunalFindingDraft(
                agent_role=TribunalAgentRole.AUDITOR,
                severity=TribunalFindingSeverity.INFO,
                subject="evidence",
                message="Comparison evidence is present and reproducible.",
                evidence={"comparison_report_id": comparison["id"]},
            )
        )

    findings.append(
        TribunalFindingDraft(
            agent_role=TribunalAgentRole.ECONOMIST,
            severity=TribunalFindingSeverity.INFO,
            subject="cost",
            message="No cost anomaly was detected in the deterministic foundation slice.",
            evidence={"cost_model": "not_configured"},
        )
    )

    blockers = [
        finding for finding in findings if finding.severity is TribunalFindingSeverity.BLOCKER
    ]
    warnings = [
        finding for finding in findings if finding.severity is TribunalFindingSeverity.WARNING
    ]
    if blockers:
        outcome = TribunalVerdictOutcome.BLOCKED
        primary_reason = blockers[0].message
    elif warnings:
        outcome = TribunalVerdictOutcome.CONDITIONAL
        primary_reason = warnings[0].message
    else:
        outcome = TribunalVerdictOutcome.APPROVED
        primary_reason = "All deterministic Tribunal agents approve the run."

    arguments.append(
        TribunalArgumentDraft(
            round=TribunalRound.VERDICT,
            agent_role=TribunalAgentRole.JUDGE,
            stance=_stance_for_outcome(outcome),
            message=primary_reason,
            evidence={"outcome": outcome.value},
        )
    )

    return TribunalDraft(
        outcome=outcome,
        primary_reason=primary_reason,
        findings=tuple(findings),
        arguments=tuple(arguments),
        dissent={
            "defender_supported_approval": any(
                argument.agent_role is TribunalAgentRole.DEFENDER
                and argument.stance is TribunalArgumentStance.SUPPORTS_APPROVAL
                for argument in arguments
            ),
            "auditor_blockers": len(
                [
                    finding
                    for finding in findings
                    if finding.agent_role is TribunalAgentRole.AUDITOR
                    and finding.severity is TribunalFindingSeverity.BLOCKER
                ]
            ),
        },
        evidence={
            "run": run,
            "comparison": comparison,
        },
        summary={
            "agent_count": len(TribunalAgentRole),
            "finding_count": len(findings),
            "argument_count": len(arguments),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "outcome": outcome.value,
        },
    )


def _stance_for_outcome(outcome: TribunalVerdictOutcome) -> TribunalArgumentStance:
    if outcome is TribunalVerdictOutcome.APPROVED:
        return TribunalArgumentStance.SUPPORTS_APPROVAL
    if outcome is TribunalVerdictOutcome.CONDITIONAL:
        return TribunalArgumentStance.SUPPORTS_CONDITIONAL
    return TribunalArgumentStance.SUPPORTS_BLOCK


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


class TribunalSession(Base):
    __tablename__ = "tribunal_sessions"
    __table_args__ = (
        CheckConstraint(f"state IN ({_SESSION_STATES})", name="ck_tribunal_sessions_state"),
        CheckConstraint(f"outcome IN ({_VERDICT_OUTCOMES})", name="ck_tribunal_sessions_outcome"),
        UniqueConstraint("run_id", name="uq_tribunal_sessions_run_id"),
        Index("ix_tribunal_sessions_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[TribunalSessionState] = mapped_column(
        String(32),
        nullable=False,
        default=TribunalSessionState.COMPLETED,
        server_default=TribunalSessionState.COMPLETED.value,
    )
    outcome: Mapped[TribunalVerdictOutcome] = mapped_column(String(32), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_by: Mapped[str | None] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TribunalBlackboardEntry(Base):
    __tablename__ = "tribunal_blackboard_entries"
    __table_args__ = (
        CheckConstraint(f"round IN ({_ROUNDS})", name="ck_tribunal_blackboard_entries_round"),
        CheckConstraint(
            f"agent_role IN ({_AGENT_ROLES})", name="ck_tribunal_blackboard_entries_agent_role"
        ),
        UniqueConstraint("session_id", "sequence", name="uq_tribunal_blackboard_session_sequence"),
        Index("ix_tribunal_blackboard_entries_session_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("tribunal_sessions.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    round: Mapped[TribunalRound] = mapped_column(String(32), nullable=False)
    agent_role: Mapped[TribunalAgentRole] = mapped_column(String(32), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TribunalFinding(Base):
    __tablename__ = "tribunal_findings"
    __table_args__ = (
        CheckConstraint(f"agent_role IN ({_AGENT_ROLES})", name="ck_tribunal_findings_agent_role"),
        CheckConstraint(
            f"severity IN ({_FINDING_SEVERITIES})", name="ck_tribunal_findings_severity"
        ),
        Index("ix_tribunal_findings_session_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("tribunal_sessions.id", ondelete="CASCADE"), nullable=False
    )
    agent_role: Mapped[TribunalAgentRole] = mapped_column(String(32), nullable=False)
    severity: Mapped[TribunalFindingSeverity] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(String(1024), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TribunalArgument(Base):
    __tablename__ = "tribunal_arguments"
    __table_args__ = (
        CheckConstraint(f"round IN ({_ROUNDS})", name="ck_tribunal_arguments_round"),
        CheckConstraint(f"agent_role IN ({_AGENT_ROLES})", name="ck_tribunal_arguments_agent_role"),
        CheckConstraint(f"stance IN ({_ARGUMENT_STANCES})", name="ck_tribunal_arguments_stance"),
        Index("ix_tribunal_arguments_session_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("tribunal_sessions.id", ondelete="CASCADE"), nullable=False
    )
    round: Mapped[TribunalRound] = mapped_column(String(32), nullable=False)
    agent_role: Mapped[TribunalAgentRole] = mapped_column(String(32), nullable=False)
    stance: Mapped[TribunalArgumentStance] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(String(1024), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TribunalVerdict(Base):
    __tablename__ = "tribunal_verdicts"
    __table_args__ = (
        CheckConstraint(f"outcome IN ({_VERDICT_OUTCOMES})", name="ck_tribunal_verdicts_outcome"),
        UniqueConstraint("session_id", name="uq_tribunal_verdicts_session_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("tribunal_sessions.id", ondelete="CASCADE"), nullable=False
    )
    outcome: Mapped[TribunalVerdictOutcome] = mapped_column(String(32), nullable=False)
    primary_reason: Mapped[str] = mapped_column(String(1024), nullable=False)
    dissent: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
