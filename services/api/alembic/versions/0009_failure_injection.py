"""Side-effect ledger and per-item fault and budget state.

Revision ID: 0009_failure_injection
Revises: 0008_trajectory_replays
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_failure_injection"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0008_trajectory_replays"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]


def upgrade() -> None:
    op.add_column(
        "run_items",
        sa.Column("injected_fault", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "run_items",
        sa.Column(
            "budget_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_table(
        "side_effect_records",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("run_item_id", sa.String(length=26), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=False),
        sa.Column("arguments_digest", sa.String(length=64), nullable=False),
        sa.Column("applied_on_attempt", sa.Integer(), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_side_effect_records"),
        # This constraint *is* the zero-duplicate-side-effect invariant. The key
        # is stable across attempts, so a retry of an effect that already
        # reached the world cannot insert a second row — no matter which worker
        # tries, or how the first attempt died.
        sa.UniqueConstraint("idempotency_key", name="uq_side_effect_records_idempotency_key"),
        sa.CheckConstraint(
            "applied_on_attempt >= 1", name="ck_side_effect_records_attempt_positive"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_side_effect_records_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            name="fk_side_effect_records_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_item_id"],
            ["run_items.id"],
            name="fk_side_effect_records_run_item",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_side_effect_records_run_id", "side_effect_records", ["run_id"])
    op.create_index("ix_side_effect_records_run_item_id", "side_effect_records", ["run_item_id"])


def downgrade() -> None:
    op.drop_index("ix_side_effect_records_run_item_id", table_name="side_effect_records")
    op.drop_index("ix_side_effect_records_run_id", table_name="side_effect_records")
    op.drop_table("side_effect_records")
    op.drop_column("run_items", "budget_state")
    op.drop_column("run_items", "injected_fault")
