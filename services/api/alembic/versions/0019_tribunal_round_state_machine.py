"""Persist Tribunal rounds and lifecycle states.

Revision ID: 0019_tribunal_rounds
Revises: 0018_agent_version_immutability
Create Date: 2026-07-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_tribunal_rounds"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0018_agent_version_immutability"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

ROUNDS = "'evidence', 'debate', 'verdict'"
SESSION_STATES = (
    "'TRIBUNAL_QUEUED', 'TRIBUNAL_EVIDENCE', 'TRIBUNAL_DEBATE', "
    "'TRIBUNAL_VERDICT', 'PUBLISHED'"
)


def upgrade() -> None:
    op.drop_constraint("ck_tribunal_sessions_state", "tribunal_sessions", type_="check")
    op.alter_column(
        "tribunal_sessions",
        "state",
        server_default="TRIBUNAL_QUEUED",
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )
    op.execute("UPDATE tribunal_sessions SET state = 'PUBLISHED' WHERE state = 'completed'")
    op.create_check_constraint(
        "ck_tribunal_sessions_state",
        "tribunal_sessions",
        f"state IN ({SESSION_STATES})",
    )

    op.create_table(
        "tribunal_rounds",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("session_id", sa.String(length=26), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("round", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"round IN ({ROUNDS})", name="ck_tribunal_rounds_round"),
        sa.CheckConstraint(f"state IN ({SESSION_STATES})", name="ck_tribunal_rounds_state"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["tribunal_sessions.id"],
            name="fk_tribunal_rounds_session",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tribunal_rounds"),
        sa.UniqueConstraint("session_id", "round", name="uq_tribunal_rounds_session_round"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_tribunal_rounds_session_sequence"),
    )
    op.create_index("ix_tribunal_rounds_session_id", "tribunal_rounds", ["session_id"])
    op.execute(
        """
        INSERT INTO tribunal_rounds (
            id, session_id, sequence, round, state, summary, started_at, completed_at
        )
        SELECT
            left(md5(session_id || ':' || round), 26),
            session_id,
            sequence,
            round,
            state,
            summary,
            started_at,
            completed_at
        FROM (
            SELECT
                tribunal_sessions.id AS session_id,
                1 AS sequence,
                'evidence' AS round,
                'TRIBUNAL_EVIDENCE' AS state,
                jsonb_build_object('backfilled', true) AS summary,
                tribunal_sessions.created_at AS started_at,
                tribunal_sessions.completed_at AS completed_at
            FROM tribunal_sessions
            UNION ALL
            SELECT
                tribunal_sessions.id,
                2,
                'debate',
                'TRIBUNAL_DEBATE',
                jsonb_build_object('backfilled', true),
                tribunal_sessions.created_at,
                tribunal_sessions.completed_at
            FROM tribunal_sessions
            UNION ALL
            SELECT
                tribunal_sessions.id,
                3,
                'verdict',
                'TRIBUNAL_VERDICT',
                jsonb_build_object('backfilled', true, 'outcome', tribunal_sessions.outcome),
                tribunal_sessions.created_at,
                tribunal_sessions.completed_at
            FROM tribunal_sessions
        ) AS round_backfill
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tribunal_rounds_session_id", table_name="tribunal_rounds")
    op.drop_table("tribunal_rounds")

    op.drop_constraint("ck_tribunal_sessions_state", "tribunal_sessions", type_="check")
    op.execute("UPDATE tribunal_sessions SET state = 'completed' WHERE state = 'PUBLISHED'")
    op.alter_column(
        "tribunal_sessions",
        "state",
        server_default="completed",
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_tribunal_sessions_state",
        "tribunal_sessions",
        "state IN ('completed')",
    )
