"""Persistence models and redaction for execution trajectories."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
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


class TrajectoryState(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TrajectoryStepType(StrEnum):
    INPUT = "input"
    GRAPH_STATE = "graph_state"
    TOOL_CALL = "tool_call"
    EVIDENCE = "evidence"
    CHECKPOINT = "checkpoint"
    FINAL_RESULT = "final_result"
    ERROR = "error"


class ReplayMode(StrEnum):
    RECORDED = "recorded"
    LIVE = "live"
    FORKED = "forked"


class ReplayState(StrEnum):
    CREATED = "CREATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


_TRAJECTORY_STATES = ", ".join(f"'{state.value}'" for state in TrajectoryState)
_STEP_TYPES = ", ".join(f"'{step_type.value}'" for step_type in TrajectoryStepType)
_REPLAY_MODES = ", ".join(f"'{mode.value}'" for mode in ReplayMode)
_REPLAY_STATES = ", ".join(f"'{state.value}'" for state in ReplayState)
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(api[_-]?key|authorization|cookie|credential|password|private[_-]?key|secret|token)",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"(?<=.).(?=[^@\s]*@)")


class Trajectory(Base):
    __tablename__ = "trajectories"
    __table_args__ = (
        CheckConstraint(f"state IN ({_TRAJECTORY_STATES})", name="ck_trajectories_state"),
        UniqueConstraint("run_item_id", name="uq_trajectories_run_item_id"),
        Index("ix_trajectories_project_id", "project_id"),
        Index("ix_trajectories_run_id_item_index", "run_id", "item_index"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    run_item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("run_items.id", ondelete="CASCADE"), nullable=False
    )
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[TrajectoryState] = mapped_column(
        String(16),
        nullable=False,
        default=TrajectoryState.RUNNING,
        server_default=TrajectoryState.RUNNING.value,
    )
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    graph_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    final_checkpoint: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TrajectoryStep(Base):
    __tablename__ = "trajectory_steps"
    __table_args__ = (
        CheckConstraint(f"step_type IN ({_STEP_TYPES})", name="ck_trajectory_steps_type"),
        CheckConstraint("step_index >= 0", name="ck_trajectory_steps_index_non_negative"),
        UniqueConstraint("trajectory_id", "step_index", name="uq_trajectory_steps_index"),
        Index("ix_trajectory_steps_trajectory_id", "trajectory_id"),
        Index("ix_trajectory_steps_type", "step_type"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    trajectory_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("trajectories.id", ondelete="CASCADE"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[TrajectoryStepType] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    redacted_input: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    redacted_output: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    checkpoint: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    redaction_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TrajectoryCheckpoint(Base):
    __tablename__ = "trajectory_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "checkpoint_index >= 0", name="ck_trajectory_checkpoints_index_non_negative"
        ),
        UniqueConstraint(
            "trajectory_id", "checkpoint_index", name="uq_trajectory_checkpoints_index"
        ),
        Index("ix_trajectory_checkpoints_trajectory_id", "trajectory_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    trajectory_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("trajectories.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("trajectory_steps.id", ondelete="SET NULL"), nullable=True
    )
    checkpoint_index: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TrajectoryReplay(Base):
    __tablename__ = "trajectory_replays"
    __table_args__ = (
        CheckConstraint(f"mode IN ({_REPLAY_MODES})", name="ck_trajectory_replays_mode"),
        CheckConstraint(f"state IN ({_REPLAY_STATES})", name="ck_trajectory_replays_state"),
        Index("ix_trajectory_replays_trajectory_id", "trajectory_id"),
        Index("ix_trajectory_replays_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    trajectory_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("trajectories.id", ondelete="CASCADE"), nullable=False
    )
    source_checkpoint_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("trajectory_checkpoints.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[ReplayMode] = mapped_column(String(16), nullable=False)
    state: Mapped[ReplayState] = mapped_column(
        String(16),
        nullable=False,
        default=ReplayState.CREATED,
        server_default=ReplayState.CREATED.value,
    )
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    replay_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    divergence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    safety_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_by: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def redact_payload(value: Any) -> tuple[Any, dict[str, int]]:
    summary = {"keys": 0, "emails": 0}
    return _redact(value, summary), summary


def _redact(value: Any, summary: dict[str, int]) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY_PATTERN.search(key_text):
                redacted[key_text] = "[REDACTED]"
                summary["keys"] += 1
            else:
                redacted[_redact_email_text(key_text, summary)] = _redact(item, summary)
        return redacted
    if isinstance(value, list):
        return [_redact(item, summary) for item in value]
    if isinstance(value, str) and "@" in value:
        return _redact_email_text(value, summary)
    return value


def _redact_email_text(value: str, summary: dict[str, int]) -> str:
    redacted_text = _EMAIL_PATTERN.sub("*", value)
    if redacted_text != value:
        summary["emails"] += 1
    return redacted_text
