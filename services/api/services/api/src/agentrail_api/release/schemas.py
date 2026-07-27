"""Public contracts for release policies and gate evaluations."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema

from agentrail_core.release import GateOutcome

#: Free-form JSON object sent by a client. A bare ``dict[str, Any]`` generates
#: ``Record<string, never>`` in the TypeScript client — an object permitting no
#: properties — so a request body that carries one is unusable from the console.
JsonObject = Annotated[
    dict[str, Any], WithJsonSchema({"type": "object", "additionalProperties": True})
]


class CreateReleasePolicyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    definition: JsonObject


class ReleasePolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    slug: str
    version: int
    definition: dict[str, Any]
    definition_digest: str
    created_by: str | None = None
    created_at: datetime


class ReleasePolicyListResponse(BaseModel):
    items: list[ReleasePolicyResponse]


class EvaluateGateRequest(BaseModel):
    """Judge one run against one policy.

    The GitHub fields are optional throughout: the gate is fully usable with no
    integration configured, and CI can call it and read the verdict directly.
    """

    release_policy_id: str = Field(min_length=26, max_length=26)
    head_sha: str | None = Field(default=None, min_length=7, max_length=64)


class GateEvaluationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    run_id: str
    release_policy_id: str
    outcome: GateOutcome
    violations: list[dict[str, Any]]
    summary: str
    head_sha: str | None = None
    check_run: dict[str, Any] | None = None
    created_at: datetime


class GateEvaluationListResponse(BaseModel):
    items: list[GateEvaluationResponse]
