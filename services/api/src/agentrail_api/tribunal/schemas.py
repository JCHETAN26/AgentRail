"""Schemas for persisted Tribunal sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentrail_core.tribunal import (
    TribunalAgentRole,
    TribunalArgumentStance,
    TribunalFindingSeverity,
    TribunalReplayMode,
    TribunalReplayState,
    TribunalRound,
    TribunalSessionState,
    TribunalVerdictOutcome,
)


class TribunalBlackboardEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sequence: int
    round: TribunalRound
    agent_role: TribunalAgentRole
    entry_type: str
    title: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TribunalFindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_role: TribunalAgentRole
    severity: TribunalFindingSeverity
    subject: str
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TribunalArgumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    round: TribunalRound
    agent_role: TribunalAgentRole
    stance: TribunalArgumentStance
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TribunalRoundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sequence: int
    round: TribunalRound
    state: TribunalSessionState
    summary: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime | None


class TribunalVerdictResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    outcome: TribunalVerdictOutcome
    primary_reason: str
    dissent: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TribunalSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    run_id: str
    state: TribunalSessionState
    outcome: TribunalVerdictOutcome
    summary: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None
    created_at: datetime
    completed_at: datetime
    verdict: TribunalVerdictResponse
    rounds: list[TribunalRoundResponse]
    findings: list[TribunalFindingResponse]
    arguments: list[TribunalArgumentResponse]
    blackboard: list[TribunalBlackboardEntryResponse]


class CreateTribunalReplayRequest(BaseModel):
    mode: TribunalReplayMode = TribunalReplayMode.RECORDED
    prompt_version: str | None = None
    prompt_overrides: dict[TribunalAgentRole, str] | None = None
    model_overrides: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _recorded_replay_must_be_exact(self) -> CreateTribunalReplayRequest:
        if self.mode == TribunalReplayMode.RECORDED and (
            self.prompt_version or self.prompt_overrides or self.model_overrides
        ):
            raise ValueError(
                "prompt_version, prompt_overrides and model_overrides are only valid "
                "for forked Tribunal replays"
            )
        return self


class TribunalReplayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    session_id: str
    source_run_id: str
    mode: TribunalReplayMode
    state: TribunalReplayState
    outcome: TribunalVerdictOutcome
    primary_reason: str
    source_digest: str
    replay_digest: str
    request: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    divergence: dict[str, Any] = Field(default_factory=dict)
    safety_summary: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class TribunalReplayListResponse(BaseModel):
    items: list[TribunalReplayResponse]
