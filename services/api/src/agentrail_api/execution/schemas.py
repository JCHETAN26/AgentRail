"""Public contracts for durable evaluation execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentrail_core.execution import EvaluationRunState, RunItemState

ExecutionMode = Literal["recorded"]


class CreateEvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_suite_id: str = Field(min_length=26, max_length=26)
    candidate_agent_version_id: str = Field(min_length=26, max_length=26)
    baseline_agent_version_id: str | None = Field(default=None, min_length=26, max_length=26)
    execution_mode: ExecutionMode = "recorded"
    #: Which pull request this run judges, when CI started it. Optional
    #: throughout — a run launched from the console has no pull request — but
    #: without these the release gate cannot publish a Check Run and a new
    #: commit cannot supersede the run it replaces.
    github_owner: str | None = Field(default=None, max_length=200)
    github_repository: str | None = Field(default=None, max_length=200)
    github_pull_number: int | None = Field(default=None, ge=1)
    github_head_sha: str | None = Field(default=None, min_length=7, max_length=64)


class EvaluationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    evaluation_suite_id: str
    candidate_agent_version_id: str
    baseline_agent_version_id: str | None = None
    execution_mode: str
    state: EvaluationRunState
    correlation_id: str
    trace_id: str
    item_count: int
    completed_count: int
    failed_count: int
    summary: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RunItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    item_index: int
    partition: str
    state: RunItemState
    attempt_count: int
    max_attempts: int
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    checkpoint: dict[str, Any]
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class EvaluationRunProgressResponse(BaseModel):
    run: EvaluationRunResponse
    item_states: dict[RunItemState, int]


class RunItemRecoveryResponse(BaseModel):
    """One item, seen from the reliability angle rather than the result angle."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    item_index: int
    partition: str
    state: RunItemState
    attempt_count: int
    max_attempts: int
    retries_remaining: int
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    lease_expired: bool
    injected_fault: dict[str, Any] | None = None
    budget_state: dict[str, Any]
    side_effect_count: int
    error_code: str | None = None
    error_message: str | None = None


class RunRecoveryResponse(BaseModel):
    run_id: str
    item_states: dict[RunItemState, int]
    #: Items whose lease has passed without the holding worker returning. These
    #: are what the recovery sweep will reclaim on its next pass.
    stranded_count: int
    #: Items that have consumed at least one retry.
    retried_count: int
    #: Effects recorded across the whole run. Compare against the number of
    #: items that reached a side-effecting step: they must never exceed it.
    side_effect_count: int
    items: list[RunItemRecoveryResponse]
