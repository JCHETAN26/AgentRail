"""Public contracts for agent definitions and immutable versions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    slug: str
    description: str | None = None
    created_at: datetime


class CreateAgentDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class AgentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    agent_id: str
    version: int
    content_digest: str = Field(description="SHA-256 digest of the canonical version payload.")
    graph_spec: dict[str, Any]
    prompt_bundle: dict[str, Any]
    model_settings: dict[str, Any] = Field(alias="model_config")
    tool_contracts: list[dict[str, Any]]
    policy_bundle: dict[str, Any]
    source_commit: str | None = None
    created_at: datetime


class CreateAgentVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    graph_spec: dict[str, Any] = Field(default_factory=dict)
    prompt_bundle: dict[str, Any] = Field(default_factory=dict)
    model_settings: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    tool_contracts: list[dict[str, Any]] = Field(default_factory=list)
    policy_bundle: dict[str, Any] = Field(default_factory=dict)
    source_commit: str | None = Field(default=None, min_length=7, max_length=64)
