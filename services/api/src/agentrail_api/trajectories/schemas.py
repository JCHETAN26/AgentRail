"""Public contracts for trajectory exploration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from agentrail_core.execution import RunItemState
from agentrail_core.trajectories import ReplayMode, ReplayState, TrajectoryState, TrajectoryStepType


class RunItemTraceResponse(BaseModel):
    id: str
    run_id: str
    item_index: int
    partition: str
    state: RunItemState
    trajectory_id: str | None = None
    failing_step_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class RunItemTraceListResponse(BaseModel):
    items: list[RunItemTraceResponse]


class TrajectoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    run_id: str
    run_item_id: str
    item_index: int
    state: TrajectoryState
    summary: dict[str, Any]
    graph_state: dict[str, Any]
    final_checkpoint: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class TrajectoryStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trajectory_id: str
    step_index: int
    step_type: TrajectoryStepType
    title: str
    redacted_input: dict[str, Any]
    redacted_output: dict[str, Any]
    evidence: dict[str, Any]
    checkpoint: dict[str, Any]
    redaction_summary: dict[str, Any]
    latency_ms: int | None = None
    created_at: datetime


class TrajectoryStepListResponse(BaseModel):
    items: list[TrajectoryStepResponse]


class TrajectoryCheckpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trajectory_id: str
    step_id: str | None = None
    checkpoint_index: int
    label: str
    state: dict[str, Any]
    created_at: datetime


class TrajectoryCheckpointListResponse(BaseModel):
    items: list[TrajectoryCheckpointResponse]


class CreateTrajectoryReplayRequest(BaseModel):
    mode: ReplayMode = ReplayMode.RECORDED
    checkpoint_id: str | None = None
    fork_overrides: dict[str, Any] | None = None


class TrajectoryReplayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    trajectory_id: str
    source_checkpoint_id: str | None = None
    mode: ReplayMode
    state: ReplayState
    source_digest: str
    replay_digest: str
    request: dict[str, Any]
    result: dict[str, Any]
    divergence: dict[str, Any]
    safety_summary: dict[str, Any]
    created_by: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class TrajectoryReplayListResponse(BaseModel):
    items: list[TrajectoryReplayResponse]
