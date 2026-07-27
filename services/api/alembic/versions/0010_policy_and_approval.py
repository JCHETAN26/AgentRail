"""Approval requests, the awaiting-approval item state and ledger approval columns.

Revision ID: 0010_policy_and_approval
Revises: 0009_failure_injection
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_policy_and_approval"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0009_failure_injection"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

_APPROVAL_STATES = "'PENDING', 'APPROVED', 'REJECTED', 'WITHDRAWN'"

#: Every run-item state, including the new AWAITING_APPROVAL. The check
#: constraint is rewritten wholesale rather than amended, because it was
#: created as one literal list in 0005.
_ITEM_STATES = (
    "'PENDING', 'LEASED', 'EXECUTING', 'EVALUATING', 'AWAITING_APPROVAL', "
    "'COMPLETED', 'FAILED_RETRYABLE', 'FAILED_TERMINAL', 'CANCELLED'"
)
_ITEM_STATES_BEFORE = (
    "'PENDING', 'LEASED', 'EXECUTING', 'EVALUATING', "
    "'COMPLETED', 'FAILED_RETRYABLE', 'FAILED_TERMINAL', 'CANCELLED'"
)


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("run_item_id", sa.String(length=26), nullable=False),
        sa.Column("trajectory_id", sa.String(length=26), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column(
            "requested_arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("edited_arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.String(length=1024), nullable=True),
        sa.Column("decided_by", sa.String(length=26), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id", name="pk_approval_requests"),
        sa.CheckConstraint(f"state IN ({_APPROVAL_STATES})", name="ck_approval_requests_state"),
        # One request per intended effect, so a retry or a redelivery asks the
        # same question once instead of queueing duplicates for a reviewer.
        sa.UniqueConstraint("idempotency_key", name="uq_approval_requests_idempotency_key"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_approval_requests_project", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["evaluation_runs.id"], name="fk_approval_requests_run", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_item_id"],
            ["run_items.id"],
            name="fk_approval_requests_run_item",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trajectory_id"],
            ["trajectories.id"],
            name="fk_approval_requests_trajectory",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name="fk_approval_requests_decided_by", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_approval_requests_run_id", "approval_requests", ["run_id"])
    op.create_index(
        "ix_approval_requests_project_state", "approval_requests", ["project_id", "state"]
    )

    op.add_column(
        "side_effect_records",
        sa.Column(
            "required_approval", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "side_effect_records", sa.Column("approval_id", sa.String(length=26), nullable=True)
    )
    op.create_foreign_key(
        "fk_side_effect_records_approval",
        "side_effect_records",
        "approval_requests",
        ["approval_id"],
        ["id"],
        # RESTRICT, not CASCADE: deleting an approval must not quietly orphan
        # the evidence that an effect was authorised.
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_side_effect_records_approved",
        "side_effect_records",
        "required_approval = false OR approval_id IS NOT NULL",
    )

    op.drop_constraint("ck_run_items_state", "run_items", type_="check")
    op.create_check_constraint("ck_run_items_state", "run_items", f"state IN ({_ITEM_STATES})")


def downgrade() -> None:
    # Any item parked for a human has no representable state once the new value
    # is gone. Cancel them rather than leave rows that violate the restored
    # constraint — they were waiting on an approval that no longer exists.
    op.execute(
        "UPDATE run_items SET state = 'CANCELLED', completed_at = now() "
        "WHERE state = 'AWAITING_APPROVAL'"
    )
    op.drop_constraint("ck_run_items_state", "run_items", type_="check")
    op.create_check_constraint(
        "ck_run_items_state", "run_items", f"state IN ({_ITEM_STATES_BEFORE})"
    )

    op.drop_constraint("ck_side_effect_records_approved", "side_effect_records", type_="check")
    op.drop_constraint("fk_side_effect_records_approval", "side_effect_records", type_="foreignkey")
    op.drop_column("side_effect_records", "approval_id")
    op.drop_column("side_effect_records", "required_approval")

    op.drop_index("ix_approval_requests_project_state", table_name="approval_requests")
    op.drop_index("ix_approval_requests_run_id", table_name="approval_requests")
    op.drop_table("approval_requests")
