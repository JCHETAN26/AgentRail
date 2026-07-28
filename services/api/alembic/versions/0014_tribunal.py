"""Multi-agent Tribunal persistence.

Revision ID: 0014_tribunal
Revises: 0013_quota_periods
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_tribunal"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0013_quota_periods"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

AGENT_ROLES = "'prosecutor', 'defender', 'auditor', 'economist', 'historian', 'judge'"
ROUNDS = "'evidence', 'debate', 'verdict'"
FINDING_SEVERITIES = "'info', 'warning', 'blocker'"
ARGUMENT_STANCES = "'supports_approval', 'supports_conditional', 'supports_block'"
VERDICT_OUTCOMES = "'approved', 'conditional', 'blocked'"
SESSION_STATES = "'completed'"


def upgrade() -> None:
    op.create_table(
        "tribunal_sessions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="completed", nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(f"state IN ({SESSION_STATES})", name="ck_tribunal_sessions_state"),
        sa.CheckConstraint(f"outcome IN ({VERDICT_OUTCOMES})", name="ck_tribunal_sessions_outcome"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_tribunal_sessions_project", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            name="fk_tribunal_sessions_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tribunal_sessions"),
        sa.UniqueConstraint("run_id", name="uq_tribunal_sessions_run_id"),
    )
    op.create_index("ix_tribunal_sessions_project_id", "tribunal_sessions", ["project_id"])

    op.create_table(
        "tribunal_blackboard_entries",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("session_id", sa.String(length=26), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("round", sa.String(length=32), nullable=False),
        sa.Column("agent_role", sa.String(length=32), nullable=False),
        sa.Column("entry_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(f"round IN ({ROUNDS})", name="ck_tribunal_blackboard_entries_round"),
        sa.CheckConstraint(
            f"agent_role IN ({AGENT_ROLES})", name="ck_tribunal_blackboard_entries_agent_role"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["tribunal_sessions.id"],
            name="fk_tribunal_blackboard_entries_session",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tribunal_blackboard_entries"),
        sa.UniqueConstraint(
            "session_id", "sequence", name="uq_tribunal_blackboard_session_sequence"
        ),
    )
    op.create_index(
        "ix_tribunal_blackboard_entries_session_id",
        "tribunal_blackboard_entries",
        ["session_id"],
    )

    op.create_table(
        "tribunal_findings",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("session_id", sa.String(length=26), nullable=False),
        sa.Column("agent_role", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("message", sa.String(length=1024), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            f"agent_role IN ({AGENT_ROLES})", name="ck_tribunal_findings_agent_role"
        ),
        sa.CheckConstraint(
            f"severity IN ({FINDING_SEVERITIES})", name="ck_tribunal_findings_severity"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["tribunal_sessions.id"],
            name="fk_tribunal_findings_session",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tribunal_findings"),
    )
    op.create_index("ix_tribunal_findings_session_id", "tribunal_findings", ["session_id"])

    op.create_table(
        "tribunal_arguments",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("session_id", sa.String(length=26), nullable=False),
        sa.Column("round", sa.String(length=32), nullable=False),
        sa.Column("agent_role", sa.String(length=32), nullable=False),
        sa.Column("stance", sa.String(length=32), nullable=False),
        sa.Column("message", sa.String(length=1024), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(f"round IN ({ROUNDS})", name="ck_tribunal_arguments_round"),
        sa.CheckConstraint(
            f"agent_role IN ({AGENT_ROLES})", name="ck_tribunal_arguments_agent_role"
        ),
        sa.CheckConstraint(f"stance IN ({ARGUMENT_STANCES})", name="ck_tribunal_arguments_stance"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["tribunal_sessions.id"],
            name="fk_tribunal_arguments_session",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tribunal_arguments"),
    )
    op.create_index("ix_tribunal_arguments_session_id", "tribunal_arguments", ["session_id"])

    op.create_table(
        "tribunal_verdicts",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("session_id", sa.String(length=26), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("primary_reason", sa.String(length=1024), nullable=False),
        sa.Column(
            "dissent",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(f"outcome IN ({VERDICT_OUTCOMES})", name="ck_tribunal_verdicts_outcome"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["tribunal_sessions.id"],
            name="fk_tribunal_verdicts_session",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tribunal_verdicts"),
        sa.UniqueConstraint("session_id", name="uq_tribunal_verdicts_session_id"),
    )


def downgrade() -> None:
    op.drop_table("tribunal_verdicts")
    op.drop_index("ix_tribunal_arguments_session_id", table_name="tribunal_arguments")
    op.drop_table("tribunal_arguments")
    op.drop_index("ix_tribunal_findings_session_id", table_name="tribunal_findings")
    op.drop_table("tribunal_findings")
    op.drop_index(
        "ix_tribunal_blackboard_entries_session_id", table_name="tribunal_blackboard_entries"
    )
    op.drop_table("tribunal_blackboard_entries")
    op.drop_index("ix_tribunal_sessions_project_id", table_name="tribunal_sessions")
    op.drop_table("tribunal_sessions")
