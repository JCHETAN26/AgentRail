"""Tribunal use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.auth.service import Actor
from agentrail_api.identity.service import record_audit
from agentrail_api.tribunal.schemas import (
    TribunalArgumentResponse,
    TribunalBlackboardEntryResponse,
    TribunalFindingResponse,
    TribunalSessionResponse,
    TribunalVerdictResponse,
)
from agentrail_core.evaluators import ComparisonReport
from agentrail_core.execution import EvaluationRun
from agentrail_core.identity import Permission, Principal, authorize
from agentrail_core.ids import new_sortable_id
from agentrail_core.tribunal import (
    TribunalArgument,
    TribunalBlackboardEntry,
    TribunalFinding,
    TribunalRound,
    TribunalSession,
    TribunalSessionState,
    TribunalVerdict,
    decide_tribunal,
)


@dataclass(frozen=True, slots=True)
class TribunalBundle:
    session: TribunalSession
    verdict: TribunalVerdict
    findings: list[TribunalFinding]
    arguments: list[TribunalArgument]
    blackboard: list[TribunalBlackboardEntry]


async def create_tribunal_session(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    run: EvaluationRun,
) -> tuple[TribunalBundle, bool]:
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)
    existing = await get_tribunal_session(session, principal, run=run)
    if existing is not None:
        return existing, False

    comparison = await session.scalar(
        select(ComparisonReport).where(ComparisonReport.run_id == run.id)
    )
    draft = decide_tribunal(run=_run_evidence(run), comparison=_comparison_evidence(comparison))
    now = datetime.now(UTC)
    tribunal = TribunalSession(
        id=new_sortable_id(),
        project_id=run.project_id,
        run_id=run.id,
        state=TribunalSessionState.COMPLETED,
        outcome=draft.outcome,
        summary=draft.summary,
        created_by=actor.user.id if actor.user else None,
        created_at=now,
        completed_at=now,
    )
    session.add(tribunal)
    await session.flush()

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
    session.add_all([*findings, *arguments, *blackboard, verdict])
    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="tribunal.created",
        target_type="evaluation_run",
        target_id=run.id,
        context={"tribunal_session_id": tribunal.id, "outcome": draft.outcome.value},
    )
    return TribunalBundle(tribunal, verdict, findings, arguments, blackboard), True


async def get_tribunal_session(
    session: AsyncSession,
    principal: Principal,
    *,
    run: EvaluationRun,
) -> TribunalBundle | None:
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    tribunal = await session.scalar(
        select(TribunalSession).where(
            TribunalSession.run_id == run.id,
            TribunalSession.project_id == run.project_id,
        )
    )
    if tribunal is None:
        return None
    verdict = await session.scalar(
        select(TribunalVerdict).where(TribunalVerdict.session_id == tribunal.id)
    )
    if verdict is None:  # pragma: no cover - created in same transaction
        return None
    findings = list(
        (
            await session.scalars(
                select(TribunalFinding)
                .where(TribunalFinding.session_id == tribunal.id)
                .order_by(TribunalFinding.id)
            )
        ).all()
    )
    arguments = list(
        (
            await session.scalars(
                select(TribunalArgument)
                .where(TribunalArgument.session_id == tribunal.id)
                .order_by(TribunalArgument.id)
            )
        ).all()
    )
    blackboard = list(
        (
            await session.scalars(
                select(TribunalBlackboardEntry)
                .where(TribunalBlackboardEntry.session_id == tribunal.id)
                .order_by(TribunalBlackboardEntry.sequence)
            )
        ).all()
    )
    return TribunalBundle(tribunal, verdict, findings, arguments, blackboard)


def as_response(bundle: TribunalBundle) -> TribunalSessionResponse:
    return TribunalSessionResponse(
        id=bundle.session.id,
        project_id=bundle.session.project_id,
        run_id=bundle.session.run_id,
        state=bundle.session.state,
        outcome=bundle.session.outcome,
        summary=bundle.session.summary,
        created_by=bundle.session.created_by,
        created_at=bundle.session.created_at,
        completed_at=bundle.session.completed_at,
        verdict=TribunalVerdictResponse.model_validate(bundle.verdict),
        findings=[TribunalFindingResponse.model_validate(finding) for finding in bundle.findings],
        arguments=[
            TribunalArgumentResponse.model_validate(argument) for argument in bundle.arguments
        ],
        blackboard=[
            TribunalBlackboardEntryResponse.model_validate(entry) for entry in bundle.blackboard
        ],
    )


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
