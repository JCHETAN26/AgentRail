"""Deterministic multi-agent Tribunal decisions and persistence models."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol

import httpx
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


class TribunalModelOutputError(ValueError):
    """Raised when an untrusted Tribunal model response does not match schema."""


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


class TribunalMode(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL_BACKED = "model_backed"


class TribunalReplayMode(StrEnum):
    RECORDED = "recorded"
    FORKED = "forked"


class TribunalReplayState(StrEnum):
    CREATED = "CREATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


_AGENT_ROLES = ", ".join(f"'{role.value}'" for role in TribunalAgentRole)
_ROUNDS = ", ".join(f"'{round_.value}'" for round_ in TribunalRound)
_FINDING_SEVERITIES = ", ".join(f"'{severity.value}'" for severity in TribunalFindingSeverity)
_ARGUMENT_STANCES = ", ".join(f"'{stance.value}'" for stance in TribunalArgumentStance)
_VERDICT_OUTCOMES = ", ".join(f"'{outcome.value}'" for outcome in TribunalVerdictOutcome)
_SESSION_STATES = ", ".join(f"'{state.value}'" for state in TribunalSessionState)
_REPLAY_MODES = ", ".join(f"'{mode.value}'" for mode in TribunalReplayMode)
_REPLAY_STATES = ", ".join(f"'{state.value}'" for state in TribunalReplayState)
DEFAULT_TRIBUNAL_PROMPT_VERSION = "tribunal-roles-v1"
MAX_SANDBOX_COLLECTION_ITEMS = 25


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


@dataclass(frozen=True, slots=True)
class TribunalPromptVersion:
    role: TribunalAgentRole
    version: str
    system_prompt: str
    response_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TribunalModelRequest:
    role: TribunalAgentRole
    round: TribunalRound
    prompt: TribunalPromptVersion
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TribunalModelResponse:
    content: dict[str, Any]
    provider: str
    model: str
    response_id: str
    usage: dict[str, Any]


class TribunalModelClient(Protocol):
    async def complete(self, request: TribunalModelRequest) -> TribunalModelResponse:
        """Return one schema-shaped role response for the Tribunal."""


def validate_tribunal_config(thresholds: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize ``thresholds.tribunal`` suite configuration."""
    raw = thresholds.get("tribunal")
    if raw is None:
        return {
            "enabled": False,
            "mode": TribunalMode.DETERMINISTIC.value,
            "prompt_version": DEFAULT_TRIBUNAL_PROMPT_VERSION,
            "model_provider": "recorded",
            "model": "tribunal-recorded-v1",
        }
    if not isinstance(raw, dict):
        raise TribunalConfigError("thresholds.tribunal must be an object.")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise TribunalConfigError("thresholds.tribunal.enabled must be a boolean.")
    mode = raw.get("mode", TribunalMode.DETERMINISTIC.value)
    if not isinstance(mode, str):
        raise TribunalConfigError("thresholds.tribunal.mode must be a string.")
    if mode not in {mode_.value for mode_ in TribunalMode}:
        raise TribunalConfigError("thresholds.tribunal.mode must be deterministic or model_backed.")
    prompt_version = raw.get("prompt_version", DEFAULT_TRIBUNAL_PROMPT_VERSION)
    if not isinstance(prompt_version, str) or not prompt_version.strip():
        raise TribunalConfigError("thresholds.tribunal.prompt_version must be a non-empty string.")
    model_provider = raw.get("model_provider", "recorded")
    if not isinstance(model_provider, str) or not model_provider.strip():
        raise TribunalConfigError("thresholds.tribunal.model_provider must be a non-empty string.")
    model = raw.get("model", "tribunal-recorded-v1")
    if not isinstance(model, str) or not model.strip():
        raise TribunalConfigError("thresholds.tribunal.model must be a non-empty string.")
    return {
        "enabled": enabled,
        "mode": mode,
        "prompt_version": prompt_version.strip(),
        "model_provider": model_provider.strip(),
        "model": model.strip(),
    }


def tribunal_enabled(thresholds: dict[str, Any]) -> bool:
    return bool(validate_tribunal_config(thresholds)["enabled"])


def default_tribunal_prompt_versions(
    version: str = DEFAULT_TRIBUNAL_PROMPT_VERSION,
    prompt_overrides: dict[TribunalAgentRole, str] | None = None,
) -> dict[TribunalAgentRole, TribunalPromptVersion]:
    """Return immutable prompt metadata for the built-in Tribunal role set."""
    shared_schema = {
        "type": "object",
        "required": ["severity", "subject", "message", "stance", "argument"],
        "properties": {
            "severity": {"enum": [severity.value for severity in TribunalFindingSeverity]},
            "subject": {"type": "string"},
            "message": {"type": "string"},
            "stance": {"enum": [stance.value for stance in TribunalArgumentStance]},
            "argument": {"type": "string"},
        },
    }
    judge_schema = {
        "type": "object",
        "required": ["outcome", "primary_reason", "dissent"],
        "properties": {
            "outcome": {"enum": [outcome.value for outcome in TribunalVerdictOutcome]},
            "primary_reason": {"type": "string"},
            "dissent": {"type": "object"},
        },
    }
    prompts = {
        TribunalAgentRole.PROSECUTOR: (
            "Find evidence that the candidate should not ship. Name concrete regressions, "
            "quality risks and missing proof."
        ),
        TribunalAgentRole.DEFENDER: (
            "Find the strongest evidence that the candidate is acceptable to ship. Be candid "
            "about remaining review needs."
        ),
        TribunalAgentRole.AUDITOR: (
            "Validate evidence quality, reproducibility and policy compliance. Block when "
            "release evidence is missing or untrustworthy."
        ),
        TribunalAgentRole.ECONOMIST: (
            "Review cost, latency and operational tradeoffs using only provided evidence."
        ),
        TribunalAgentRole.HISTORIAN: (
            "Summarize the run history and comparison context without inventing facts."
        ),
        TribunalAgentRole.JUDGE: (
            "Render the final Tribunal verdict from the role evidence. Auditor blockers are "
            "binding and model output is untrusted."
        ),
    }
    if prompt_overrides:
        prompts = {**prompts, **prompt_overrides}
    return {
        role: TribunalPromptVersion(
            role=role,
            version=version,
            system_prompt=prompt,
            response_schema=judge_schema if role is TribunalAgentRole.JUDGE else shared_schema,
        )
        for role, prompt in prompts.items()
    }


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


class RecordedTribunalModelClient:
    """Deterministic model-client stand-in for CI, demos and recorded replay.

    It speaks through the same protocol as a live provider would, so the
    Tribunal orchestration can prove prompt provenance, schema validation and
    blackboard persistence without paid credentials or network access.
    """

    def __init__(self, *, provider: str = "recorded", model: str = "tribunal-recorded-v1") -> None:
        self.provider = provider
        self.model = model

    async def complete(self, request: TribunalModelRequest) -> TribunalModelResponse:
        draft = _recorded_role_response(request.role, request.evidence)
        return TribunalModelResponse(
            content=draft,
            provider=self.provider,
            model=self.model,
            response_id=f"{request.prompt.version}:{request.role.value}",
            usage={"input_tokens": 0, "output_tokens": 0, "recorded": True},
        )


class OpenAITribunalModelClient:
    """OpenAI Responses API adapter for model-backed Tribunal roles."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise TribunalConfigError("OpenAI Tribunal model provider requires an API key.")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    async def complete(self, request: TribunalModelRequest) -> TribunalModelResponse:
        payload = {
            "model": self._model,
            "input": [
                {"role": "system", "content": request.prompt.system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "role": request.role.value,
                            "round": request.round.value,
                            "evidence": request.evidence,
                        },
                        sort_keys=True,
                        default=str,
                    ),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": f"agentrail_tribunal_{request.role.value}",
                    "schema": request.prompt.response_schema,
                    "strict": True,
                }
            },
        }
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            transport=self._transport,
        ) as client:
            response = await client.post(
                "/responses",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise TribunalModelOutputError(
                    f"OpenAI Tribunal request failed with HTTP {exc.response.status_code}."
                ) from exc
            body = response.json()
        content = _openai_structured_content(body)
        return TribunalModelResponse(
            content=content,
            provider="openai",
            model=str(body.get("model") or self._model),
            response_id=str(body.get("id") or ""),
            usage=_optional_dict(body.get("usage")),
        )


def build_tribunal_model_client(
    config: dict[str, Any],
    *,
    openai_api_key: str | None = None,
    openai_base_url: str = "https://api.openai.com/v1",
    timeout_seconds: float = 60.0,
) -> TribunalModelClient:
    provider = str(config.get("model_provider", "recorded"))
    model = str(config.get("model", "tribunal-recorded-v1"))
    if provider == "recorded":
        return RecordedTribunalModelClient(provider=provider, model=model)
    if provider == "openai":
        if not openai_api_key:
            raise TribunalConfigError(
                "thresholds.tribunal.model_provider is openai but AGENTRAIL_OPENAI_API_KEY "
                "is not configured."
            )
        return OpenAITribunalModelClient(
            api_key=openai_api_key,
            model=model,
            base_url=openai_base_url,
            timeout_seconds=timeout_seconds,
        )
    raise TribunalConfigError("thresholds.tribunal.model_provider must be recorded or openai.")


def sandbox_tribunal_model_evidence(
    *,
    run: dict[str, Any],
    comparison: dict[str, Any] | None,
    deterministic_floor: TribunalDraft,
    prompt_overrides: dict[TribunalAgentRole, str] | None = None,
) -> dict[str, Any]:
    """Return model-safe Tribunal evidence without raw untrusted text.

    Model-backed Tribunal roles need metrics and verdict context, not arbitrary
    strings from trajectories, evaluator output or user-supplied prompt forks.
    Every string is reduced to digest/length metadata before it can enter a
    model prompt.
    """
    sandboxed = {
        "run": _sandbox_json_value(run),
        "comparison": _sandbox_json_value(comparison),
        "deterministic_floor": {
            "outcome": deterministic_floor.outcome.value,
            "primary_reason": _sandbox_string(deterministic_floor.primary_reason),
            "summary": _sandbox_json_value(deterministic_floor.summary),
        },
        "prompt_overrides": {
            role.value: _sandbox_string(prompt) for role, prompt in (prompt_overrides or {}).items()
        },
    }
    return {
        "sandbox": {
            "version": "tribunal-evidence-sandbox-v1",
            "untrusted_text_policy": "raw strings are replaced with sha256 and length metadata",
        },
        "evidence": sandboxed,
        "summary": _sandbox_summary(sandboxed),
    }


async def decide_model_backed_tribunal(
    *,
    run: dict[str, Any],
    comparison: dict[str, Any] | None,
    model_client: TribunalModelClient,
    prompt_version: str = DEFAULT_TRIBUNAL_PROMPT_VERSION,
    prompt_overrides: dict[TribunalAgentRole, str] | None = None,
) -> TribunalDraft:
    """Run the prompt-versioned Tribunal orchestration through a model client.

    Model output never gets trusted directly. Each role response is shape-checked
    and converted into the same persisted findings/arguments as the deterministic
    path. The deterministic Tribunal remains a safety floor: missing or
    non-reproducible comparison evidence still blocks even if the Judge model
    attempts to approve.
    """
    prompts = default_tribunal_prompt_versions(prompt_version, prompt_overrides=prompt_overrides)
    deterministic_floor = decide_tribunal(run=run, comparison=comparison)
    evidence = sandbox_tribunal_model_evidence(
        run=run,
        comparison=comparison,
        deterministic_floor=deterministic_floor,
        prompt_overrides=prompt_overrides,
    )
    findings: list[TribunalFindingDraft] = []
    arguments: list[TribunalArgumentDraft] = []
    model_calls: list[dict[str, Any]] = []
    try:
        for role in (
            TribunalAgentRole.HISTORIAN,
            TribunalAgentRole.PROSECUTOR,
            TribunalAgentRole.DEFENDER,
            TribunalAgentRole.AUDITOR,
            TribunalAgentRole.ECONOMIST,
        ):
            response = await model_client.complete(
                TribunalModelRequest(
                    role=role,
                    round=TribunalRound.EVIDENCE,
                    prompt=prompts[role],
                    evidence=evidence,
                )
            )
            model_calls.append(
                _model_call_summary(role=role, response=response, prompt=prompts[role])
            )
            findings.append(_finding_from_model(role, response))
            arguments.append(_argument_from_model(role, TribunalRound.DEBATE, response))

        judge_response = await model_client.complete(
            TribunalModelRequest(
                role=TribunalAgentRole.JUDGE,
                round=TribunalRound.VERDICT,
                prompt=prompts[TribunalAgentRole.JUDGE],
                evidence={
                    **evidence,
                    "findings": _sandbox_json_value(
                        [finding.evidence | {"message": finding.message} for finding in findings]
                    ),
                    "arguments": _sandbox_json_value(
                        [
                            argument.evidence | {"message": argument.message}
                            for argument in arguments
                        ]
                    ),
                },
            )
        )
        model_calls.append(
            _model_call_summary(
                role=TribunalAgentRole.JUDGE,
                response=judge_response,
                prompt=prompts[TribunalAgentRole.JUDGE],
            )
        )
        outcome = TribunalVerdictOutcome(_required_str(judge_response.content, "outcome"))
        primary_reason = _bounded_text(judge_response.content.get("primary_reason"))
        dissent = _optional_dict(judge_response.content.get("dissent"))
    except (ValueError, TribunalModelOutputError) as invalid:
        return _blocked_for_model_output(run, comparison, prompt_version, str(invalid))

    floor_blockers = [
        finding
        for finding in deterministic_floor.findings
        if finding.severity is TribunalFindingSeverity.BLOCKER
    ]
    if floor_blockers:
        outcome = TribunalVerdictOutcome.BLOCKED
        primary_reason = floor_blockers[0].message
        dissent = {
            **dissent,
            "deterministic_floor_override": True,
            "model_judge_outcome": _required_str(judge_response.content, "outcome"),
        }

    auditor_blockers = [
        finding
        for finding in findings
        if finding.agent_role is TribunalAgentRole.AUDITOR
        and finding.severity is TribunalFindingSeverity.BLOCKER
    ]
    if auditor_blockers:
        outcome = TribunalVerdictOutcome.BLOCKED
        primary_reason = auditor_blockers[0].message
        dissent = {
            **dissent,
            "auditor_model_override": True,
            "model_judge_outcome": _required_str(judge_response.content, "outcome"),
        }

    blockers = [
        finding for finding in findings if finding.severity is TribunalFindingSeverity.BLOCKER
    ]
    warnings = [
        finding for finding in findings if finding.severity is TribunalFindingSeverity.WARNING
    ]
    arguments.append(
        TribunalArgumentDraft(
            round=TribunalRound.VERDICT,
            agent_role=TribunalAgentRole.JUDGE,
            stance=_stance_for_outcome(outcome),
            message=primary_reason,
            evidence={
                "outcome": outcome.value,
                "mode": TribunalMode.MODEL_BACKED.value,
                "prompt_version": prompt_version,
                "model_response": _model_call_summary(
                    role=TribunalAgentRole.JUDGE,
                    response=judge_response,
                    prompt=prompts[TribunalAgentRole.JUDGE],
                ),
            },
        )
    )
    return TribunalDraft(
        outcome=outcome,
        primary_reason=primary_reason,
        findings=tuple(findings),
        arguments=tuple(arguments),
        dissent=dissent,
        evidence={
            "run": run,
            "comparison": comparison,
            "mode": TribunalMode.MODEL_BACKED.value,
            "prompt_version": prompt_version,
            "prompt_overrides": {
                role.value: prompt for role, prompt in (prompt_overrides or {}).items()
            },
            "model_calls": model_calls,
            "model_evidence_sandbox": evidence["summary"],
        },
        summary={
            "mode": TribunalMode.MODEL_BACKED.value,
            "prompt_version": prompt_version,
            "prompt_override_roles": sorted(role.value for role in (prompt_overrides or {})),
            "agent_count": len(TribunalAgentRole),
            "finding_count": len(findings),
            "argument_count": len(arguments),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "outcome": outcome.value,
            "model_call_count": len(model_calls),
        },
    )


async def create_or_get_tribunal_session(
    db: AsyncSession,
    *,
    run: EvaluationRun,
    comparison: ComparisonReport | None,
    created_by: str | None = None,
    tribunal_config: dict[str, Any] | None = None,
    model_client: TribunalModelClient | None = None,
) -> tuple[TribunalPersistenceBundle, bool]:
    existing = await get_persisted_tribunal_session(db, run_id=run.id, project_id=run.project_id)
    if existing is not None:
        return existing, False

    config = (
        validate_tribunal_config({"tribunal": tribunal_config})
        if tribunal_config is not None
        else validate_tribunal_config({})
    )
    run_evidence = _run_evidence(run)
    comparison_evidence = _comparison_evidence(comparison)
    if config.get("mode") == TribunalMode.MODEL_BACKED.value:
        client = model_client or build_tribunal_model_client(config)
        draft = await decide_model_backed_tribunal(
            run=run_evidence,
            comparison=comparison_evidence,
            model_client=client,
            prompt_version=str(config.get("prompt_version", DEFAULT_TRIBUNAL_PROMPT_VERSION)),
        )
    else:
        draft = decide_tribunal(run=run_evidence, comparison=comparison_evidence)
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


def _sandbox_json_value(value: Any, *, _depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sandbox_string(value)
    if isinstance(value, dict):
        if _depth >= 6:
            return {"kind": "sandboxed_depth_limit", "sha256": _digest(value)}
        return {
            str(key): _sandbox_json_value(child, _depth=_depth + 1)
            for key, child in list(value.items())[:MAX_SANDBOX_COLLECTION_ITEMS]
        } | _collection_limit_metadata(value)
    if isinstance(value, (list, tuple)):
        if _depth >= 6:
            return {"kind": "sandboxed_depth_limit", "sha256": _digest(value)}
        return [
            _sandbox_json_value(child, _depth=_depth + 1)
            for child in list(value)[:MAX_SANDBOX_COLLECTION_ITEMS]
        ]
    return _sandbox_string(str(value))


def _sandbox_string(value: str) -> dict[str, Any]:
    return {
        "kind": "untrusted_text",
        "sha256": sha256(value.encode("utf-8")).hexdigest(),
        "length": len(value),
    }


def _collection_limit_metadata(value: dict[str, Any]) -> dict[str, Any]:
    if len(value) <= MAX_SANDBOX_COLLECTION_ITEMS:
        return {}
    return {"_sandbox_truncated_keys": len(value) - MAX_SANDBOX_COLLECTION_ITEMS}


def _sandbox_summary(value: Any) -> dict[str, Any]:
    encoded = json.dumps(value, sort_keys=True, default=str)
    return {
        "sha256": sha256(encoded.encode("utf-8")).hexdigest(),
        "byte_length": len(encoded.encode("utf-8")),
        "untrusted_string_count": _count_untrusted_strings(value),
    }


def _count_untrusted_strings(value: Any) -> int:
    if isinstance(value, dict):
        if value.get("kind") == "untrusted_text":
            return 1
        return sum(_count_untrusted_strings(child) for child in value.values())
    if isinstance(value, list):
        return sum(_count_untrusted_strings(child) for child in value)
    return 0


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


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


def _recorded_role_response(role: TribunalAgentRole, evidence: dict[str, Any]) -> dict[str, Any]:
    evidence_payload = evidence.get("evidence", evidence)
    comparison = evidence_payload.get("comparison") if isinstance(evidence_payload, dict) else None
    summary = comparison.get("summary", {}) if isinstance(comparison, dict) else {}
    run = evidence_payload.get("run", {}) if isinstance(evidence_payload, dict) else {}
    pass_rate = _number(summary.get("pass_rate"))
    regression_count = int(summary.get("regression_count") or 0)
    reproducible = bool(summary.get("reproducible", False))
    failed_count = int(run.get("failed_count") or 0) if isinstance(run, dict) else 0
    if role is TribunalAgentRole.JUDGE:
        floor = (
            evidence_payload.get("deterministic_floor", {})
            if isinstance(evidence_payload, dict)
            else {}
        )
        floor_outcome = floor.get("outcome") if isinstance(floor, dict) else None
        if floor_outcome == TribunalVerdictOutcome.BLOCKED.value:
            return {
                "outcome": TribunalVerdictOutcome.BLOCKED.value,
                "primary_reason": str(floor.get("primary_reason") or "Evidence failed audit."),
                "dissent": {"deterministic_floor_override": True},
            }
        if failed_count > 0 or pass_rate < 1.0 or regression_count > 0:
            return {
                "outcome": TribunalVerdictOutcome.CONDITIONAL.value,
                "primary_reason": "Recorded Judge requires review of quality warnings.",
                "dissent": {"quality_review_required": True},
            }
        return {
            "outcome": TribunalVerdictOutcome.APPROVED.value,
            "primary_reason": "Recorded Judge approves the clean, reproducible run.",
            "dissent": {},
        }
    if role is TribunalAgentRole.AUDITOR and comparison is None:
        return {
            "severity": TribunalFindingSeverity.BLOCKER.value,
            "subject": "evidence",
            "message": "Comparison evidence is missing, so the Tribunal cannot approve the run.",
            "stance": TribunalArgumentStance.SUPPORTS_BLOCK.value,
            "argument": "Approval requires a comparison report.",
        }
    if role is TribunalAgentRole.AUDITOR and not reproducible:
        return {
            "severity": TribunalFindingSeverity.BLOCKER.value,
            "subject": "reproducibility",
            "message": "The comparison report does not claim reproducibility.",
            "stance": TribunalArgumentStance.SUPPORTS_BLOCK.value,
            "argument": "Non-reproducible evidence cannot support release approval.",
        }
    if role is TribunalAgentRole.PROSECUTOR and (
        failed_count > 0 or pass_rate < 1.0 or regression_count > 0
    ):
        return {
            "severity": TribunalFindingSeverity.WARNING.value,
            "subject": "quality",
            "message": "The candidate has failures, regressions or an incomplete pass rate.",
            "stance": TribunalArgumentStance.SUPPORTS_CONDITIONAL.value,
            "argument": "Quality evidence requires human review before approval.",
        }
    if role is TribunalAgentRole.DEFENDER:
        clean = pass_rate >= 1.0 and failed_count == 0 and regression_count == 0
        return {
            "severity": TribunalFindingSeverity.INFO.value,
            "subject": "defense",
            "message": (
                "The defense found no recorded evidence against approval."
                if clean
                else "The defense recommends targeted review instead of automatic rejection."
            ),
            "stance": (
                TribunalArgumentStance.SUPPORTS_APPROVAL.value
                if clean
                else TribunalArgumentStance.SUPPORTS_CONDITIONAL.value
            ),
            "argument": (
                "The run is clean on recorded quality evidence."
                if clean
                else "The candidate may still be acceptable after review of flagged evidence."
            ),
        }
    if role is TribunalAgentRole.ECONOMIST:
        return {
            "severity": TribunalFindingSeverity.INFO.value,
            "subject": "cost",
            "message": "No cost anomaly was detected by the recorded Tribunal client.",
            "stance": TribunalArgumentStance.SUPPORTS_APPROVAL.value,
            "argument": "No provided cost evidence argues against release.",
        }
    return {
        "severity": TribunalFindingSeverity.INFO.value,
        "subject": "run",
        "message": "The recorded Tribunal client summarized the available run context.",
        "stance": TribunalArgumentStance.SUPPORTS_APPROVAL.value,
        "argument": "The run has enough recorded context for debate.",
    }


def _finding_from_model(
    role: TribunalAgentRole, response: TribunalModelResponse
) -> TribunalFindingDraft:
    return TribunalFindingDraft(
        agent_role=role,
        severity=TribunalFindingSeverity(_required_str(response.content, "severity")),
        subject=_bounded_text(response.content.get("subject"), limit=64),
        message=_bounded_text(response.content.get("message")),
        evidence={
            "mode": TribunalMode.MODEL_BACKED.value,
            "model_response": {
                "provider": response.provider,
                "model": response.model,
                "response_id": response.response_id,
                "usage": response.usage,
            },
        },
    )


def _argument_from_model(
    role: TribunalAgentRole, round_: TribunalRound, response: TribunalModelResponse
) -> TribunalArgumentDraft:
    return TribunalArgumentDraft(
        round=round_,
        agent_role=role,
        stance=TribunalArgumentStance(_required_str(response.content, "stance")),
        message=_bounded_text(response.content.get("argument")),
        evidence={
            "mode": TribunalMode.MODEL_BACKED.value,
            "finding_message": _bounded_text(response.content.get("message")),
            "model_response": {
                "provider": response.provider,
                "model": response.model,
                "response_id": response.response_id,
                "usage": response.usage,
            },
        },
    )


def _blocked_for_model_output(
    run: dict[str, Any],
    comparison: dict[str, Any] | None,
    prompt_version: str,
    reason: str,
) -> TribunalDraft:
    finding = TribunalFindingDraft(
        agent_role=TribunalAgentRole.AUDITOR,
        severity=TribunalFindingSeverity.BLOCKER,
        subject="model_output",
        message="A Tribunal model response failed schema validation.",
        evidence={"reason": reason},
    )
    argument = TribunalArgumentDraft(
        round=TribunalRound.VERDICT,
        agent_role=TribunalAgentRole.JUDGE,
        stance=TribunalArgumentStance.SUPPORTS_BLOCK,
        message="The Tribunal failed closed because model output was invalid.",
        evidence={"outcome": TribunalVerdictOutcome.BLOCKED.value},
    )
    return TribunalDraft(
        outcome=TribunalVerdictOutcome.BLOCKED,
        primary_reason=finding.message,
        findings=(finding,),
        arguments=(argument,),
        dissent={"model_output_invalid": True},
        evidence={
            "run": run,
            "comparison": comparison,
            "mode": TribunalMode.MODEL_BACKED.value,
            "prompt_version": prompt_version,
            "model_output_error": reason,
        },
        summary={
            "mode": TribunalMode.MODEL_BACKED.value,
            "prompt_version": prompt_version,
            "agent_count": len(TribunalAgentRole),
            "finding_count": 1,
            "argument_count": 1,
            "blocker_count": 1,
            "warning_count": 0,
            "outcome": TribunalVerdictOutcome.BLOCKED.value,
            "model_call_count": 0,
        },
    )


def _model_call_summary(
    *,
    role: TribunalAgentRole,
    response: TribunalModelResponse,
    prompt: TribunalPromptVersion,
) -> dict[str, Any]:
    return {
        "role": role.value,
        "provider": response.provider,
        "model": response.model,
        "response_id": response.response_id,
        "prompt_version": prompt.version,
        "usage": response.usage,
    }


def _required_str(content: dict[str, Any], key: str) -> str:
    value = content.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TribunalModelOutputError(f"{key} must be a non-empty string")
    return value.strip()


def _bounded_text(value: Any, *, limit: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TribunalModelOutputError("text fields must be non-empty strings")
    return value.strip()[:limit]


def _optional_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TribunalModelOutputError("dissent must be an object")
    return value


def _openai_structured_content(body: dict[str, Any]) -> dict[str, Any]:
    candidates: list[Any] = []
    if isinstance(body.get("output_text"), str):
        candidates.append(body["output_text"])
    output = body.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict):
                    parsed_part = part.get("parsed")
                    if isinstance(parsed_part, dict):
                        return parsed_part
                    if isinstance(part.get("text"), str):
                        candidates.append(part["text"])
    choices = body.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                candidates.append(message["content"])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise TribunalModelOutputError("OpenAI response did not contain structured JSON content.")


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


class TribunalReplay(Base):
    __tablename__ = "tribunal_replays"
    __table_args__ = (
        CheckConstraint(f"mode IN ({_REPLAY_MODES})", name="ck_tribunal_replays_mode"),
        CheckConstraint(f"state IN ({_REPLAY_STATES})", name="ck_tribunal_replays_state"),
        CheckConstraint(f"outcome IN ({_VERDICT_OUTCOMES})", name="ck_tribunal_replays_outcome"),
        Index("ix_tribunal_replays_session_id", "session_id"),
        Index("ix_tribunal_replays_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("tribunal_sessions.id", ondelete="CASCADE"), nullable=False
    )
    source_run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[TribunalReplayMode] = mapped_column(String(16), nullable=False)
    state: Mapped[TribunalReplayState] = mapped_column(
        String(16),
        nullable=False,
        default=TribunalReplayState.CREATED,
        server_default=TribunalReplayState.CREATED.value,
    )
    outcome: Mapped[TribunalVerdictOutcome] = mapped_column(String(32), nullable=False)
    primary_reason: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    replay_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    divergence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    safety_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_by: Mapped[str | None] = mapped_column(String(26), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
