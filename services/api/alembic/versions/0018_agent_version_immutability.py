"""Reject direct updates to agent versions.

Revision ID: 0018_agent_version_immutability
Revises: 0017_api_key_usage_anomaly
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018_agent_version_immutability"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0017_api_key_usage_anomaly"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_agent_version_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'agent_versions are immutable after creation'
                USING ERRCODE = 'check_violation';
        END;
        $$;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS reject_agent_version_update ON agent_versions;
        CREATE TRIGGER reject_agent_version_update
        BEFORE UPDATE ON agent_versions
        FOR EACH ROW
        EXECUTE FUNCTION reject_agent_version_update();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS reject_agent_version_update ON agent_versions;")
    op.execute("DROP FUNCTION IF EXISTS reject_agent_version_update();")
