"""Trajectory replay records.

Revision ID: 0008_trajectory_replays
Revises: 0007_evaluators_comparison
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_trajectory_replays"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0007_evaluators_comparison"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

_REPLAY_MODES = "'recorded', 'live', 'forked'"
_REPLAY_STATES = "'CREATED', 'COMPLETED', 'FAILED'"


def upgrade() -> None:
    op.create_table(
        "trajectory_replays",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("trajectory_id", sa.String(length=26), nullable=False),
        sa.Column("source_checkpoint_id", sa.String(length=26), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="CREATED"),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("replay_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "request",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "divergence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "safety_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_trajectory_replays"),
        sa.CheckConstraint(f"mode IN ({_REPLAY_MODES})", name="ck_trajectory_replays_mode"),
        sa.CheckConstraint(f"state IN ({_REPLAY_STATES})", name="ck_trajectory_replays_state"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_trajectory_replays_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trajectory_id"],
            ["trajectories.id"],
            name="fk_trajectory_replays_trajectory",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_checkpoint_id"],
            ["trajectory_checkpoints.id"],
            name="fk_trajectory_replays_source_checkpoint",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_trajectory_replays_created_by",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_trajectory_replays_trajectory_id", "trajectory_replays", ["trajectory_id"])
    op.create_index("ix_trajectory_replays_project_id", "trajectory_replays", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_trajectory_replays_project_id", table_name="trajectory_replays")
    op.drop_index("ix_trajectory_replays_trajectory_id", table_name="trajectory_replays")
    op.drop_table("trajectory_replays")
