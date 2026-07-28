"""Deterministic Tribunal decisions."""

from __future__ import annotations

import pytest

from agentrail_core.tribunal import (
    DEFAULT_TRIBUNAL_PROMPT_VERSION,
    RecordedTribunalModelClient,
    TribunalAgentRole,
    TribunalConfigError,
    TribunalFindingSeverity,
    TribunalMode,
    TribunalModelRequest,
    TribunalModelResponse,
    TribunalVerdictOutcome,
    decide_model_backed_tribunal,
    decide_tribunal,
    default_tribunal_prompt_versions,
    validate_tribunal_config,
)

RUN = {
    "id": "01KTRIBUNAL00000000000000",
    "project_id": "01KPROJECT000000000000000",
    "state": "PASSED",
    "item_count": 16,
    "completed_count": 16,
    "failed_count": 0,
    "summary": {},
}


def comparison(*, pass_rate: float = 1.0, reproducible: bool = True) -> dict[str, object]:
    return {
        "id": "01KREPORT0000000000000000",
        "summary": {
            "pass_rate": pass_rate,
            "regression_count": 0,
            "reproducible": reproducible,
        },
        "evaluator_metrics": {},
        "category_metrics": {},
        "regressions": [],
    }


def test_clean_reproducible_evidence_is_approved() -> None:
    verdict = decide_tribunal(run=RUN, comparison=comparison())

    assert verdict.outcome is TribunalVerdictOutcome.APPROVED
    assert verdict.summary["agent_count"] == 6
    assert {finding.agent_role for finding in verdict.findings} >= {
        TribunalAgentRole.PROSECUTOR,
        TribunalAgentRole.DEFENDER,
        TribunalAgentRole.AUDITOR,
        TribunalAgentRole.ECONOMIST,
        TribunalAgentRole.HISTORIAN,
    }


def test_auditor_blocker_overrides_defender_approval() -> None:
    verdict = decide_tribunal(run=RUN, comparison=comparison(reproducible=False))

    assert verdict.outcome is TribunalVerdictOutcome.BLOCKED
    assert verdict.dissent["defender_supported_approval"] is True
    assert verdict.dissent["auditor_blockers"] == 1


def test_quality_warning_becomes_conditional() -> None:
    verdict = decide_tribunal(run=RUN | {"failed_count": 1}, comparison=comparison(pass_rate=0.9))

    assert verdict.outcome is TribunalVerdictOutcome.CONDITIONAL
    assert verdict.summary["warning_count"] == 1


def test_default_prompt_versions_cover_every_tribunal_role() -> None:
    prompts = default_tribunal_prompt_versions()

    assert set(prompts) == set(TribunalAgentRole)
    assert prompts[TribunalAgentRole.JUDGE].version == DEFAULT_TRIBUNAL_PROMPT_VERSION
    assert "outcome" in prompts[TribunalAgentRole.JUDGE].response_schema["required"]


@pytest.mark.asyncio
async def test_recorded_model_backed_tribunal_approves_clean_evidence() -> None:
    verdict = await decide_model_backed_tribunal(
        run=RUN,
        comparison=comparison(),
        model_client=RecordedTribunalModelClient(),
    )

    assert verdict.outcome is TribunalVerdictOutcome.APPROVED
    assert verdict.summary["mode"] == TribunalMode.MODEL_BACKED.value
    assert verdict.summary["model_call_count"] == 6
    assert all(
        finding.evidence["model_response"]["provider"] == "recorded" for finding in verdict.findings
    )


@pytest.mark.asyncio
async def test_model_backed_tribunal_keeps_auditor_blocker_as_safety_floor() -> None:
    verdict = await decide_model_backed_tribunal(
        run=RUN,
        comparison=comparison(reproducible=False),
        model_client=RecordedTribunalModelClient(),
    )

    assert verdict.outcome is TribunalVerdictOutcome.BLOCKED
    assert verdict.dissent["deterministic_floor_override"] is True


class BadModelClient:
    async def complete(self, request: TribunalModelRequest) -> TribunalModelResponse:
        return TribunalModelResponse(
            content={"severity": "info"},
            provider="bad",
            model="bad-output",
            response_id=request.role.value,
            usage={},
        )


class AuditorBlocksJudgeApprovesClient:
    async def complete(self, request: TribunalModelRequest) -> TribunalModelResponse:
        if request.role is TribunalAgentRole.JUDGE:
            content = {
                "outcome": "approved",
                "primary_reason": "Judge attempted to approve despite Auditor blocker.",
                "dissent": {},
            }
        else:
            content = {
                "severity": ("blocker" if request.role is TribunalAgentRole.AUDITOR else "info"),
                "subject": request.role.value,
                "message": f"{request.role.value} finding",
                "stance": (
                    "supports_block"
                    if request.role is TribunalAgentRole.AUDITOR
                    else "supports_approval"
                ),
                "argument": f"{request.role.value} argument",
            }
        return TribunalModelResponse(
            content=content,
            provider="test",
            model="auditor-blocks",
            response_id=request.role.value,
            usage={},
        )


@pytest.mark.asyncio
async def test_model_backed_tribunal_fails_closed_on_invalid_model_output() -> None:
    verdict = await decide_model_backed_tribunal(
        run=RUN,
        comparison=comparison(),
        model_client=BadModelClient(),
    )

    assert verdict.outcome is TribunalVerdictOutcome.BLOCKED
    assert verdict.findings[0].severity is TribunalFindingSeverity.BLOCKER
    assert verdict.dissent["model_output_invalid"] is True


@pytest.mark.asyncio
async def test_model_auditor_blocker_overrides_judge_approval() -> None:
    verdict = await decide_model_backed_tribunal(
        run=RUN,
        comparison=comparison(),
        model_client=AuditorBlocksJudgeApprovesClient(),
    )

    assert verdict.outcome is TribunalVerdictOutcome.BLOCKED
    assert verdict.dissent["auditor_model_override"] is True
    assert verdict.primary_reason == "auditor finding"


def test_tribunal_config_accepts_prompt_version_and_model_backed_mode() -> None:
    config = validate_tribunal_config(
        {
            "tribunal": {
                "enabled": True,
                "mode": "model_backed",
                "prompt_version": "tribunal-roles-v2",
                "model_provider": "recorded",
                "model": "recorded-v2",
            }
        }
    )

    assert config == {
        "enabled": True,
        "mode": "model_backed",
        "prompt_version": "tribunal-roles-v2",
        "model_provider": "recorded",
        "model": "recorded-v2",
    }


def test_tribunal_config_rejects_non_string_mode() -> None:
    with pytest.raises(TribunalConfigError) as caught:
        validate_tribunal_config({"tribunal": {"enabled": True, "mode": []}})

    assert "tribunal.mode" in str(caught.value)
