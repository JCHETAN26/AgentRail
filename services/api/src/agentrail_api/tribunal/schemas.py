"""Schemas for persisted Tribunal sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentrail_core.tribunal import (
    TribunalAgentRole,
    TribunalArgumentStance,
    TribunalFindingSeverity,
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
    findings: list[TribunalFindingResponse]
    arguments: list[TribunalArgumentResponse]
    blackboard: list[TribunalBlackboardEntryResponse]
