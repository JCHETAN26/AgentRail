"""Tribunal use cases."""

from __future__ import annotations

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
from agentrail_core.tribunal import (
    TribunalPersistenceBundle,
    create_or_get_tribunal_session,
    get_persisted_tribunal_session,
)


async def create_tribunal_session(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    run: EvaluationRun,
) -> tuple[TribunalPersistenceBundle, bool]:
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)
    comparison = await session.scalar(
        select(ComparisonReport).where(ComparisonReport.run_id == run.id)
    )
    bundle, created = await create_or_get_tribunal_session(
        session,
        run=run,
        comparison=comparison,
        created_by=actor.user.id if actor.user else None,
    )
    if created:
        await record_audit(
            session,
            organisation_id=principal.organisation_id,
            actor=actor,
            action="tribunal.created",
            target_type="evaluation_run",
            target_id=run.id,
            context={
                "tribunal_session_id": bundle.session.id,
                "outcome": bundle.session.outcome.value,
            },
        )
    return bundle, created


async def get_tribunal_session(
    session: AsyncSession,
    principal: Principal,
    *,
    run: EvaluationRun,
) -> TribunalPersistenceBundle | None:
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    return await get_persisted_tribunal_session(session, run_id=run.id, project_id=run.project_id)


def as_response(bundle: TribunalPersistenceBundle) -> TribunalSessionResponse:
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
