"""Persistence models for durable evaluation execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentrail_core.db import Base
from agentrail_core.execution.state import EvaluationRunState, RunItemState

_RUN_STATES = ", ".join(f"'{state.value}'" for state in EvaluationRunState)
_ITEM_STATES = ", ".join(f"'{state.value}'" for state in RunItemState)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(f"state IN ({_RUN_STATES})", name="ck_evaluation_runs_state"),
        CheckConstraint("item_count >= 0", name="ck_evaluation_runs_item_count_non_negative"),
        CheckConstraint("completed_count >= 0", name="ck_evaluation_runs_completed_non_negative"),
        CheckConstraint("failed_count >= 0", name="ck_evaluation_runs_failed_non_negative"),
        UniqueConstraint(
            "project_id", "idempotency_key", name="uq_evaluation_runs_project_idempotency_key"
        ),
        Index("ix_evaluation_runs_project_id", "project_id"),
        Index("ix_evaluation_runs_state_created_at", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    evaluation_suite_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evaluation_suites.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_agent_version_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False
    )
    baseline_agent_version_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=True
    )
    execution_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="recorded", server_default="recorded"
    )
    state: Mapped[EvaluationRunState] = mapped_column(
        String(16),
        nullable=False,
        default=EvaluationRunState.CREATED,
        server_default=EvaluationRunState.CREATED.value,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class RunItem(Base):
    __tablename__ = "run_items"
    __table_args__ = (
        CheckConstraint(f"state IN ({_ITEM_STATES})", name="ck_run_items_state"),
        CheckConstraint("attempt_count >= 0", name="ck_run_items_attempt_count_non_negative"),
        CheckConstraint("max_attempts >= 1", name="ck_run_items_max_attempts_positive"),
        UniqueConstraint("run_id", "item_index", name="uq_run_items_run_index"),
        Index("ix_run_items_run_id", "run_id"),
        Index("ix_run_items_state_lease_expires_at", "state", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    partition: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default", server_default="default"
    )
    state: Mapped[RunItemState] = mapped_column(
        String(24),
        nullable=False,
        default=RunItemState.PENDING,
        server_default=RunItemState.PENDING.value,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checkpoint: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: The fault injected into the most recent attempt, or null for a clean run.
    #: Kept per item rather than per attempt because the trajectory already
    #: carries the full per-attempt history; this is the recovery view's index.
    injected_fault: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: Budget limits, spend and remaining headroom for this item.
    budget_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_published_at_created_at", "published_at", "created_at"),
        Index("ix_outbox_events_aggregate", "aggregate_type", "aggregate_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(26), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
