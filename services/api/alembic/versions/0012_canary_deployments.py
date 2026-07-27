"""Canary deployments and rollback history.

Revision ID: 0012_canary_deployments
Revises: 0011_release_gates
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_canary_deployments"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0011_release_gates"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("gate_evaluation_id", sa.String(length=26), nullable=True),
        sa.Column("candidate_agent_version_id", sa.String(length=26), nullable=False),
        sa.Column("environment", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="canary"),
        sa.Column("traffic_percent", sa.Integer(), nullable=False),
        sa.Column(
            "workload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "baseline_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "canary_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "deltas",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "decision",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("rollback_reason", sa.String(length=1024), nullable=True),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_deployments"),
        sa.CheckConstraint(
            "state IN ('canary', 'promoted', 'rolled_back')", name="ck_deployments_state"
        ),
        sa.CheckConstraint(
            "traffic_percent >= 0 AND traffic_percent <= 100", name="ck_deployments_traffic"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_deployments_project", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            name="fk_deployments_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["gate_evaluation_id"],
            ["gate_evaluations.id"],
            name="fk_deployments_gate_evaluation",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_deployments_created_by", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_deployments_project_id", "deployments", ["project_id"])
    op.create_index("ix_deployments_run_id", "deployments", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_deployments_run_id", table_name="deployments")
    op.drop_index("ix_deployments_project_id", table_name="deployments")
    op.drop_table("deployments")
