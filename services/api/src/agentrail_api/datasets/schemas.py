"""Public contracts for datasets, dataset versions and suites."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DatasetInputFormat = Literal["jsonl", "csv"]


class DatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    slug: str
    description: str | None = None
    created_at: datetime


class CreateDatasetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class DatasetVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    dataset_id: str
    version: int
    content_digest: str = Field(description="SHA-256 digest of the uploaded dataset bytes.")
    storage_uri: str
    input_format: str
    source_filename: str | None = None
    record_schema: dict[str, Any] = Field(alias="schema")
    validation_report: dict[str, Any]
    item_count: int
    rejected_count: int
    partition_counts: dict[str, int]
    created_at: datetime


class CreateDatasetVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_format: DatasetInputFormat
    content: str = Field(min_length=1)
    source_filename: str | None = Field(default=None, max_length=255)
    storage_uri: str | None = Field(default=None, max_length=512)


class DatasetValidationResponse(BaseModel):
    version_id: str
    dataset_id: str
    validation_report: dict[str, Any]


class EvaluationSuiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    dataset_version_id: str
    name: str
    slug: str
    description: str | None = None
    evaluators: list[dict[str, Any]]
    thresholds: dict[str, Any]
    fault_profiles: list[dict[str, Any]]
    preview: dict[str, Any]
    frozen_at: datetime | None = None
    created_at: datetime


class CreateEvaluationSuiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    dataset_version_id: str = Field(min_length=26, max_length=26)
    description: str | None = Field(default=None, max_length=1000)
    evaluators: list[dict[str, Any]] = Field(default_factory=list)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    fault_profiles: list[dict[str, Any]] = Field(default_factory=list)
