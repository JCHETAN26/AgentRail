"""Tribunal use cases."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.auth.service import Actor, principal_for_organisation
from agentrail_api.identity.service import record_audit
from agentrail_api.tribunal.schemas import (
    CreateTribunalReplayRequest,
    TribunalArgumentResponse,
    TribunalBlackboardEntryResponse,
    TribunalFindingResponse,
    TribunalReplayResponse,
    TribunalSessionResponse,
    TribunalVerdictResponse,
)
from agentrail_core.datasets import EvaluationSuite
from agentrail_core.errors import ForbiddenError, ValidationFailedError
from agentrail_core.evaluators import ComparisonReport
from agentrail_core.execution import EvaluationRun
from agentrail_core.identity import Permission, Principal, Project, authorize
from agentrail_core.ids import new_sortable_id
from agentrail_core.trajectories import redact_payload
from agentrail_core.tribunal import (
    RecordedTribunalModelClient,
    TribunalConfigError,
    TribunalMode,
    TribunalPersistenceBundle,
    TribunalReplay,
    TribunalReplayMode,
    TribunalReplayState,
    TribunalSession,
    TribunalVerdictOutcome,
    build_tribunal_model_client,
    create_or_get_tribunal_session,
    decide_model_backed_tribunal,
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


async def principal_for_tribunal_session(
    session: AsyncSession, actor: Actor, tribunal_session_id: str
) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(TribunalSession, TribunalSession.project_id == Project.id)
        .where(TribunalSession.id == tribunal_session_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def list_replays(
    session: AsyncSession,
    principal: Principal,
    *,
    tribunal_session_id: str,
) -> list[TribunalReplay]:
    await _tribunal_bundle_for_session(session, principal, tribunal_session_id=tribunal_session_id)
    rows = await session.scalars(
        select(TribunalReplay)
        .where(TribunalReplay.session_id == tribunal_session_id)
        .order_by(TribunalReplay.created_at, TribunalReplay.id)
    )
    return list(rows.all())


async def create_replay(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    tribunal_session_id: str,
    request: CreateTribunalReplayRequest,
) -> TribunalReplay:
    bundle = await _tribunal_bundle_for_session(
        session, principal, tribunal_session_id=tribunal_session_id
    )
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)
    source_digest = _tribunal_source_digest(bundle)
    run, comparison = _source_evidence(bundle)
    prompt_version = request.prompt_version or str(
        bundle.session.summary.get("prompt_version") or "tribunal-roles-v1"
    )
    model_overrides = request.model_overrides or {}
    requested_providers = [
        str(provider)
        for provider in (model_overrides.get("provider"), model_overrides.get("model_provider"))
        if provider is not None
    ]
    invalid_provider = next(
        (provider for provider in requested_providers if provider != "recorded"), None
    )
    if invalid_provider is not None:
        raise ValidationFailedError(
            "Tribunal replays are recorded-only in this release.",
            details={"model_provider": invalid_provider},
        )
    model = str(model_overrides.get("model") or "tribunal-recorded-v1")
    prompt_overrides = request.prompt_overrides or {}
    draft = await decide_model_backed_tribunal(
        run=run,
        comparison=comparison,
        model_client=RecordedTribunalModelClient(model=model),
        prompt_version=prompt_version,
        prompt_overrides=prompt_overrides,
    )
    raw_request = request.model_dump(mode="json")
    raw_overrides = {
        "prompt_version": request.prompt_version,
        "prompt_overrides": raw_request.get("prompt_overrides") or {},
        "model_overrides": request.model_overrides or {},
    }
    redacted_request, request_redaction = redact_payload(raw_request)
    draft_digest = _tribunal_draft_digest(draft)
    replay_digest = (
        draft_digest
        if request.mode == TribunalReplayMode.RECORDED
        else _digest(
            {
                "source_digest": source_digest,
                "draft_digest": draft_digest,
                "mode": request.mode,
                "fork": raw_overrides,
                "outcome": draft.outcome.value,
                "primary_reason": draft.primary_reason,
            }
        )
    )
    divergence = _tribunal_divergence_summary(
        mode=request.mode,
        source_outcome=bundle.session.outcome,
        replay_outcome=draft.outcome,
        source_digest=source_digest,
        replay_digest=replay_digest,
        raw_overrides=raw_overrides,
    )
    source_outcome_value = _enum_value(bundle.session.outcome)
    replay_outcome_value = _enum_value(draft.outcome)
    now = datetime.now(UTC)
    replay = TribunalReplay(
        id=new_sortable_id(),
        project_id=bundle.session.project_id,
        session_id=bundle.session.id,
        source_run_id=bundle.session.run_id,
        mode=request.mode,
        state=TribunalReplayState.COMPLETED,
        outcome=draft.outcome,
        primary_reason=draft.primary_reason,
        source_digest=source_digest,
        replay_digest=replay_digest,
        request={
            **redacted_request,
            "redaction_summary": {"request": request_redaction},
        },
        result={
            "reproduced": replay_digest == source_digest
            and replay_outcome_value == source_outcome_value,
            "source_outcome": source_outcome_value,
            "replay_outcome": replay_outcome_value,
            "summary": draft.summary,
            "dissent": draft.dissent,
            "evidence": {
                "prompt_version": prompt_version,
                "prompt_override_roles": sorted(role.value for role in prompt_overrides),
                "model_provider": "recorded",
                "model": model,
            },
        },
        divergence=divergence,
        safety_summary={
            "side_effect_policy": "never_mutate_source_session",
            "source_session_mutated": False,
            "live_model_calls": 0,
            "executed_live": False,
            "recorded_model_calls": draft.summary.get("model_call_count", 0),
        },
        created_by=actor.user.id if actor.user else None,
        completed_at=now,
    )
    session.add(replay)
    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="tribunal_replay.created",
        target_type="tribunal_replay",
        target_id=replay.id,
        context={
            "tribunal_session_id": bundle.session.id,
            "mode": request.mode,
            "source_digest": source_digest,
            "replay_digest": replay_digest,
            "outcome": draft.outcome.value,
        },
    )
    await session.flush()
    return replay


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


def replay_as_response(replay: TribunalReplay) -> TribunalReplayResponse:
    return TribunalReplayResponse.model_validate(replay)


async def _tribunal_bundle_for_session(
    session: AsyncSession,
    principal: Principal,
    *,
    tribunal_session_id: str,
) -> TribunalPersistenceBundle:
    tribunal_session = await session.scalar(
        select(TribunalSession)
        .join(Project, Project.id == TribunalSession.project_id)
        .where(
            TribunalSession.id == tribunal_session_id,
            Project.organisation_id == principal.organisation_id,
        )
    )
    if tribunal_session is None:
        raise ForbiddenError()
    bundle = await get_persisted_tribunal_session(
        session, run_id=tribunal_session.run_id, project_id=tribunal_session.project_id
    )
    if bundle is None:  # pragma: no cover - protected by session row + same transaction writes
        raise ForbiddenError()
    return bundle


def _source_evidence(
    bundle: TribunalPersistenceBundle,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    run = bundle.verdict.evidence.get("run")
    if not isinstance(run, dict):
        raise ValidationFailedError(
            "Tribunal session is missing replayable run evidence.",
            details={"tribunal_session_id": bundle.session.id},
        )
    comparison = bundle.verdict.evidence.get("comparison")
    if comparison is not None and not isinstance(comparison, dict):
        raise ValidationFailedError(
            "Tribunal session contains malformed comparison evidence.",
            details={"tribunal_session_id": bundle.session.id},
        )
    return run, comparison


def _tribunal_source_digest(bundle: TribunalPersistenceBundle) -> str:
    return _digest(
        {
            "summary": bundle.session.summary,
            "verdict": {
                "outcome": bundle.verdict.outcome,
                "primary_reason": bundle.verdict.primary_reason,
                "dissent": bundle.verdict.dissent,
                "evidence": bundle.verdict.evidence,
            },
            "findings": [
                {
                    "agent_role": finding.agent_role,
                    "severity": finding.severity,
                    "subject": finding.subject,
                    "message": finding.message,
                    "evidence": finding.evidence,
                }
                for finding in bundle.findings
            ],
            "arguments": [
                {
                    "round": argument.round,
                    "agent_role": argument.agent_role,
                    "stance": argument.stance,
                    "message": argument.message,
                    "evidence": argument.evidence,
                }
                for argument in bundle.arguments
            ],
        }
    )


def _tribunal_draft_digest(draft: Any) -> str:
    return _digest(
        {
            "summary": draft.summary,
            "verdict": {
                "outcome": draft.outcome,
                "primary_reason": draft.primary_reason,
                "dissent": draft.dissent,
                "evidence": draft.evidence,
            },
            "findings": [
                {
                    "agent_role": finding.agent_role,
                    "severity": finding.severity,
                    "subject": finding.subject,
                    "message": finding.message,
                    "evidence": finding.evidence,
                }
                for finding in draft.findings
            ],
            "arguments": [
                {
                    "round": argument.round,
                    "agent_role": argument.agent_role,
                    "stance": argument.stance,
                    "message": argument.message,
                    "evidence": argument.evidence,
                }
                for argument in draft.arguments
            ],
        }
    )


def _tribunal_divergence_summary(
    *,
    mode: TribunalReplayMode,
    source_outcome: TribunalVerdictOutcome,
    replay_outcome: TribunalVerdictOutcome,
    source_digest: str,
    replay_digest: str,
    raw_overrides: dict[str, Any],
) -> dict[str, Any]:
    changed_fields = sorted(
        key for key, value in raw_overrides.items() if value not in (None, {}, [])
    )
    source_outcome_value = _enum_value(source_outcome)
    replay_outcome_value = _enum_value(replay_outcome)
    return {
        "diverged": replay_digest != source_digest or replay_outcome_value != source_outcome_value,
        "mode": mode,
        "source_digest": source_digest,
        "replay_digest": replay_digest,
        "source_outcome": source_outcome_value,
        "replay_outcome": replay_outcome_value,
        "outcome_changed": replay_outcome_value != source_outcome_value,
        "changed_fields": changed_fields,
    }


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)
