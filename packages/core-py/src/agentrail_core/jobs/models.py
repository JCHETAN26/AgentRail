"""Persistence model for the Phase 0 job slice.

The ``jobs`` table is the authoritative record of a unit of work. Redis holds
only a pointer to a row that has already been committed here.
"""

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
from agentrail_core.jobs.state import JobState

_JOB_STATES = ", ".join(f"'{state.value}'" for state in JobState)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(f"state IN ({_JOB_STATES})", name="ck_jobs_state"),
        CheckConstraint("attempts >= 0", name="ck_jobs_attempts_non_negative"),
        CheckConstraint(
            "(state IN ('COMPLETED', 'FAILED')) = (completed_at IS NOT NULL)",
            name="ck_jobs_completed_at_matches_terminal_state",
        ),
        # Idempotency keys are scoped to a project, not global: two tenants must
        # be able to use the same key without colliding, and one tenant must not
        # be able to discover another's keys by probing for a 409.
        UniqueConstraint("project_id", "idempotency_key", name="uq_jobs_project_idempotency_key"),
        # Supports the worker's "oldest pending first" recovery sweep.
        Index("ix_jobs_state_created_at", "state", "created_at"),
        # Supports the tenant-scoped listing, which is always by project.
        Index("ix_jobs_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    #: Every job belongs to exactly one project, and therefore to one
    #: organisation. This column is what makes tenant scoping enforceable.
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[JobState] = mapped_column(
        String(16), nullable=False, default=JobState.PENDING, server_default=JobState.PENDING.value
    )

    #: Unique per project. This is what makes ``POST /jobs`` safe to retry.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: SHA-256 of the canonical request body, used to detect a key replayed with
    #: different content.
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: Optimistic-concurrency token. Every state change bumps it, so two workers
    #: racing on the same job cannot both win.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
