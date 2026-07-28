"""Forkable Tribunal replay persistence.

Revision ID: 0015_tribunal_replays
Revises: 0014_tribunal
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_tribunal_replays"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0014_tribunal"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

REPLAY_MODES = "'recorded', 'forked'"
REPLAY_STATES = "'CREATED', 'COMPLETED', 'FAILED'"
VERDICT_OUTCOMES = "'approved', 'conditional', 'blocked'"


def upgrade() -> None:
    op.create_table(
        "tribunal_replays",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("session_id", sa.String(length=26), nullable=False),
        sa.Column("source_run_id", sa.String(length=26), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="CREATED", nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("primary_reason", sa.String(length=1024), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("replay_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "request",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "divergence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "safety_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"mode IN ({REPLAY_MODES})", name="ck_tribunal_replays_mode"),
        sa.CheckConstraint(f"state IN ({REPLAY_STATES})", name="ck_tribunal_replays_state"),
        sa.CheckConstraint(f"outcome IN ({VERDICT_OUTCOMES})", name="ck_tribunal_replays_outcome"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_tribunal_replays_project", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["tribunal_sessions.id"],
            name="fk_tribunal_replays_session",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["evaluation_runs.id"],
            name="fk_tribunal_replays_source_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tribunal_replays"),
    )
    op.create_index("ix_tribunal_replays_project_id", "tribunal_replays", ["project_id"])
    op.create_index("ix_tribunal_replays_session_id", "tribunal_replays", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_tribunal_replays_session_id", table_name="tribunal_replays")
    op.drop_index("ix_tribunal_replays_project_id", table_name="tribunal_replays")
    op.drop_table("tribunal_replays")
