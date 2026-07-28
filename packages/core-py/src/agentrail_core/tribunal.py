"""Deterministic multi-agent Tribunal decisions and persistence models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from agentrail_core.db import Base
from agentrail_core.evaluators import ComparisonReport
from agentrail_core.execution import EvaluationRun
from agentrail_core.ids import new_sortable_id


class TribunalConfigError(ValueError):
    """Raised when suite Tribunal configuration is malformed."""


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


@dataclass(frozen=True, slots=True)
class TribunalPersistenceBundle:
    session: TribunalSession
    verdict: TribunalVerdict
    findings: list[TribunalFinding]
    arguments: list[TribunalArgument]
    blackboard: list[TribunalBlackboardEntry]


def validate_tribunal_config(thresholds: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize ``thresholds.tribunal`` suite configuration."""
    raw = thresholds.get("tribunal")
    if raw is None:
        return {"enabled": False}
    if not isinstance(raw, dict):
        raise TribunalConfigError("thresholds.tribunal must be an object.")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise TribunalConfigError("thresholds.tribunal.enabled must be a boolean.")
    return {"enabled": enabled}


def tribunal_enabled(thresholds: dict[str, Any]) -> bool:
    return bool(validate_tribunal_config(thresholds)["enabled"])


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


async def create_or_get_tribunal_session(
    db: AsyncSession,
    *,
    run: EvaluationRun,
    comparison: ComparisonReport | None,
    created_by: str | None = None,
) -> tuple[TribunalPersistenceBundle, bool]:
    existing = await get_persisted_tribunal_session(db, run_id=run.id, project_id=run.project_id)
    if existing is not None:
        return existing, False

    draft = decide_tribunal(run=_run_evidence(run), comparison=_comparison_evidence(comparison))
    now = datetime.now(UTC)
    tribunal = TribunalSession(
        id=new_sortable_id(),
        project_id=run.project_id,
        run_id=run.id,
        state=TribunalSessionState.COMPLETED,
        outcome=draft.outcome,
        summary=draft.summary,
        created_by=created_by,
        created_at=now,
        completed_at=now,
    )
    db.add(tribunal)
    await db.flush()

    findings = [
        TribunalFinding(
            id=new_sortable_id(),
            session_id=tribunal.id,
            agent_role=finding.agent_role,
            severity=finding.severity,
            subject=finding.subject,
            message=finding.message,
            evidence=finding.evidence,
            created_at=now,
        )
        for finding in draft.findings
    ]
    arguments = [
        TribunalArgument(
            id=new_sortable_id(),
            session_id=tribunal.id,
            round=argument.round,
            agent_role=argument.agent_role,
            stance=argument.stance,
            message=argument.message,
            evidence=argument.evidence,
            created_at=now,
        )
        for argument in draft.arguments
    ]
    blackboard = _blackboard_entries(tribunal.id, findings, arguments, created_at=now)
    verdict = TribunalVerdict(
        id=new_sortable_id(),
        session_id=tribunal.id,
        outcome=draft.outcome,
        primary_reason=draft.primary_reason,
        dissent=draft.dissent,
        evidence=draft.evidence,
        created_at=now,
    )
    db.add_all([*findings, *arguments, *blackboard, verdict])
    return TribunalPersistenceBundle(tribunal, verdict, findings, arguments, blackboard), True


async def get_persisted_tribunal_session(
    db: AsyncSession, *, run_id: str, project_id: str
) -> TribunalPersistenceBundle | None:
    tribunal = await db.scalar(
        select(TribunalSession).where(
            TribunalSession.run_id == run_id,
            TribunalSession.project_id == project_id,
        )
    )
    if tribunal is None:
        return None
    verdict = await db.scalar(
        select(TribunalVerdict).where(TribunalVerdict.session_id == tribunal.id)
    )
    if verdict is None:  # pragma: no cover - created in same transaction
        return None
    findings = list(
        (
            await db.scalars(
                select(TribunalFinding)
                .where(TribunalFinding.session_id == tribunal.id)
                .order_by(TribunalFinding.id)
            )
        ).all()
    )
    arguments = list(
        (
            await db.scalars(
                select(TribunalArgument)
                .where(TribunalArgument.session_id == tribunal.id)
                .order_by(TribunalArgument.id)
            )
        ).all()
    )
    blackboard = list(
        (
            await db.scalars(
                select(TribunalBlackboardEntry)
                .where(TribunalBlackboardEntry.session_id == tribunal.id)
                .order_by(TribunalBlackboardEntry.sequence)
            )
        ).all()
    )
    return TribunalPersistenceBundle(tribunal, verdict, findings, arguments, blackboard)


def _run_evidence(run: EvaluationRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "project_id": run.project_id,
        "state": getattr(run.state, "value", run.state),
        "item_count": run.item_count,
        "completed_count": run.completed_count,
        "failed_count": run.failed_count,
        "summary": run.summary,
    }


def _comparison_evidence(comparison: ComparisonReport | None) -> dict[str, Any] | None:
    if comparison is None:
        return None
    return {
        "id": comparison.id,
        "summary": comparison.summary,
        "evaluator_metrics": comparison.evaluator_metrics,
        "category_metrics": comparison.category_metrics,
        "regressions": comparison.regressions,
    }


def _blackboard_entries(
    session_id: str,
    findings: list[TribunalFinding],
    arguments: list[TribunalArgument],
    *,
    created_at: datetime,
) -> list[TribunalBlackboardEntry]:
    entries: list[TribunalBlackboardEntry] = []
    sequence = 1
    for finding in findings:
        entries.append(
            TribunalBlackboardEntry(
                id=new_sortable_id(),
                session_id=session_id,
                sequence=sequence,
                round=TribunalRound.EVIDENCE,
                agent_role=finding.agent_role,
                entry_type="finding",
                title=finding.subject,
                payload={
                    "severity": finding.severity.value,
                    "message": finding.message,
                    "evidence": finding.evidence,
                },
                created_at=created_at,
            )
        )
        sequence += 1
    for argument in arguments:
        entries.append(
            TribunalBlackboardEntry(
                id=new_sortable_id(),
                session_id=session_id,
                sequence=sequence,
                round=argument.round,
                agent_role=argument.agent_role,
                entry_type="argument",
                title=argument.stance.value,
                payload={"message": argument.message, "evidence": argument.evidence},
                created_at=created_at,
            )
        )
        sequence += 1
    return entries


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
