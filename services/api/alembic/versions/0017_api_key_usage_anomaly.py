"""API key usage anomaly metadata.

Revision ID: 0017_api_key_usage_anomaly
Revises: 0016_postgres_rls
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_api_key_usage_anomaly"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0016_postgres_rls"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("last_used_ip_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "api_keys", sa.Column("last_used_user_agent_hash", sa.String(length=64), nullable=True)
    )
    op.add_column("api_keys", sa.Column("last_anomaly_at", sa.DateTime(timezone=True)))
    op.add_column(
        "api_keys",
        sa.Column("anomaly_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "anomaly_count")
    op.drop_column("api_keys", "last_anomaly_at")
    op.drop_column("api_keys", "last_used_user_agent_hash")
    op.drop_column("api_keys", "last_used_ip_hash")
