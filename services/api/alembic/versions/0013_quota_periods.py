"""Organisation quota periods.

Revision ID: 0013_quota_periods
Revises: 0012_canary_deployments
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_quota_periods"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0012_canary_deployments"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.create_table(
        "organisation_quota_periods",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("organisation_id", sa.String(length=26), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("evaluation_item_limit", sa.Integer(), nullable=False),
        sa.Column("evaluation_items_used", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organisation_quota_periods"),
        sa.CheckConstraint(
            "evaluation_item_limit >= 1", name="ck_organisation_quota_periods_limit_positive"
        ),
        sa.CheckConstraint(
            "evaluation_items_used >= 0", name="ck_organisation_quota_periods_used_non_negative"
        ),
        sa.CheckConstraint(
            "evaluation_items_used <= evaluation_item_limit",
            name="ck_organisation_quota_periods_used_within_limit",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name="fk_organisation_quota_periods_organisation",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "uq_organisation_quota_periods_organisation_period",
        "organisation_quota_periods",
        ["organisation_id", "period_start"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_organisation_quota_periods_organisation_period",
        table_name="organisation_quota_periods",
    )
    op.drop_table("organisation_quota_periods")
