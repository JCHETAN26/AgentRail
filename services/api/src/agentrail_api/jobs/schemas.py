"""Public request and response contracts for the jobs resource.

These models are the source of the generated OpenAPI document and, through it,
the generated TypeScript client in ``packages/contracts``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentrail_core.jobs.state import JobState


class JobKind(StrEnum):
    """Job kinds the platform can execute.

    Phase 0 ships exactly one: a deterministic no-op that proves the whole
    web → API → queue → worker → sandbox → database path without requiring a
    model provider.
    """

    NOOP = "noop"


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: JobKind = Field(default=JobKind.NOOP, description="Job kind to execute.")
    message: str = Field(
        min_length=1,
        max_length=500,
        description="Echoed back by the deterministic sandbox task.",
    )


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Sortable ULID identifying the job.")
    kind: JobKind
    state: JobState
    correlation_id: str = Field(description="Quote this identifier when reporting a failure.")
    trace_id: str
    attempts: int
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class JobListResponse(BaseModel):
    items: list[JobResponse]
    next_cursor: str | None = Field(
        default=None,
        description="Pass as `cursor` to fetch the next page. Null when the list is exhausted.",
    )
