"""Trajectory capture and checkpoints.

Revision ID: 0006_trajectories
Revises: 0005_durable_execution
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_trajectories"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0005_durable_execution"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

_TRAJECTORY_STATES = "'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED'"
_STEP_TYPES = (
    "'input', 'graph_state', 'tool_call', 'evidence', 'checkpoint', 'final_result', 'error'"
)


def upgrade() -> None:
    op.create_table(
        "trajectories",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("run_item_id", sa.String(length=26), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="RUNNING"),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "graph_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "final_checkpoint",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_trajectories"),
        sa.CheckConstraint(f"state IN ({_TRAJECTORY_STATES})", name="ck_trajectories_state"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_trajectories_project", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["evaluation_runs.id"], name="fk_trajectories_run", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_item_id"], ["run_items.id"], name="fk_trajectories_run_item", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("run_item_id", name="uq_trajectories_run_item_id"),
    )
    op.create_index("ix_trajectories_project_id", "trajectories", ["project_id"])
    op.create_index("ix_trajectories_run_id_item_index", "trajectories", ["run_id", "item_index"])

    op.create_table(
        "trajectory_steps",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("trajectory_id", sa.String(length=26), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column(
            "redacted_input",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "redacted_output",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "checkpoint",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "redaction_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trajectory_steps"),
        sa.CheckConstraint(f"step_type IN ({_STEP_TYPES})", name="ck_trajectory_steps_type"),
        sa.CheckConstraint("step_index >= 0", name="ck_trajectory_steps_index_non_negative"),
        sa.ForeignKeyConstraint(
            ["trajectory_id"],
            ["trajectories.id"],
            name="fk_trajectory_steps_trajectory",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("trajectory_id", "step_index", name="uq_trajectory_steps_index"),
    )
    op.create_index("ix_trajectory_steps_trajectory_id", "trajectory_steps", ["trajectory_id"])
    op.create_index("ix_trajectory_steps_type", "trajectory_steps", ["step_type"])

    op.create_table(
        "trajectory_checkpoints",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("trajectory_id", sa.String(length=26), nullable=False),
        sa.Column("step_id", sa.String(length=26), nullable=True),
        sa.Column("checkpoint_index", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trajectory_checkpoints"),
        sa.CheckConstraint(
            "checkpoint_index >= 0", name="ck_trajectory_checkpoints_index_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["trajectory_id"],
            ["trajectories.id"],
            name="fk_trajectory_checkpoints_trajectory",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            ["trajectory_steps.id"],
            name="fk_trajectory_checkpoints_step",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "trajectory_id", "checkpoint_index", name="uq_trajectory_checkpoints_index"
        ),
    )
    op.create_index(
        "ix_trajectory_checkpoints_trajectory_id",
        "trajectory_checkpoints",
        ["trajectory_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_trajectory_checkpoints_trajectory_id", table_name="trajectory_checkpoints")
    op.drop_table("trajectory_checkpoints")
    op.drop_index("ix_trajectory_steps_type", table_name="trajectory_steps")
    op.drop_index("ix_trajectory_steps_trajectory_id", table_name="trajectory_steps")
    op.drop_table("trajectory_steps")
    op.drop_index("ix_trajectories_run_id_item_index", table_name="trajectories")
    op.drop_index("ix_trajectories_project_id", table_name="trajectories")
    op.drop_table("trajectories")
