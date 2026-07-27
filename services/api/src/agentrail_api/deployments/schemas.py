"""Public contracts for canary deployments."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentrail_api.release.schemas import JsonObject
from agentrail_core.deployments import DeploymentState


class CreateDeploymentRequest(BaseModel):
    run_id: str = Field(min_length=26, max_length=26)
    gate_evaluation_id: str | None = Field(default=None, min_length=26, max_length=26)
    environment: str = Field(default="canary", min_length=1, max_length=100)
    traffic_percent: int = Field(default=10, ge=1, le=100)
    workload: JsonObject = Field(default_factory=dict)
    baseline_metrics: JsonObject = Field(default_factory=dict)
    canary_metrics: JsonObject = Field(default_factory=dict)
    thresholds: JsonObject = Field(
        default_factory=lambda: {
            "min_success_rate": 0.95,
            "max_error_rate": 0.02,
            "max_p95_latency_delta_ms": 100,
            "max_cost_delta_per_1k": 0.05,
        }
    )


class RollbackDeploymentRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1024)


class DeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    run_id: str
    gate_evaluation_id: str | None = None
    candidate_agent_version_id: str
    environment: str
    state: DeploymentState
    traffic_percent: int
    workload: dict[str, Any]
    baseline_metrics: dict[str, Any]
    canary_metrics: dict[str, Any]
    thresholds: dict[str, Any]
    deltas: dict[str, float]
    decision: dict[str, Any]
    rollback_reason: str | None = None
    created_by: str | None = None
    created_at: datetime
    promoted_at: datetime | None = None
    rolled_back_at: datetime | None = None


class DeploymentListResponse(BaseModel):
    items: list[DeploymentResponse]
