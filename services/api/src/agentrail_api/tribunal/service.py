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
from agentrail_core.datasets import EvaluationSuite
from agentrail_core.errors import ValidationFailedError
from agentrail_core.evaluators import ComparisonReport
from agentrail_core.execution import EvaluationRun
from agentrail_core.identity import Permission, Principal, authorize
from agentrail_core.tribunal import (
    TribunalConfigError,
    TribunalMode,
    TribunalPersistenceBundle,
    build_tribunal_model_client,
    create_or_get_tribunal_session,
    get_persisted_tribunal_session,
    validate_tribunal_config,
)


async def create_tribunal_session(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    run: EvaluationRun,
    openai_api_key: str | None = None,
    openai_base_url: str = "https://api.openai.com/v1",
    model_timeout_seconds: float = 60.0,
) -> tuple[TribunalPersistenceBundle, bool]:
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)
    comparison = await session.scalar(
        select(ComparisonReport).where(ComparisonReport.run_id == run.id)
    )
    suite = await session.get(EvaluationSuite, run.evaluation_suite_id)
    tribunal_config = suite.thresholds.get("tribunal") if suite is not None else None
    model_client = None
    if tribunal_config is not None:
        try:
            parsed = validate_tribunal_config({"tribunal": tribunal_config})
            if parsed["mode"] == TribunalMode.MODEL_BACKED.value:
                model_client = build_tribunal_model_client(
                    parsed,
                    openai_api_key=openai_api_key,
                    openai_base_url=openai_base_url,
                    timeout_seconds=model_timeout_seconds,
                )
        except TribunalConfigError as invalid:
            raise ValidationFailedError(
                "Tribunal model provider configuration is invalid.",
                details={"reason": str(invalid)},
            ) from invalid
    bundle, created = await create_or_get_tribunal_session(
        session,
        run=run,
        comparison=comparison,
        created_by=actor.user.id if actor.user else None,
        tribunal_config=tribunal_config,
        model_client=model_client,
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
