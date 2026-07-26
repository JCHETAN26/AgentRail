"""Durable evaluation execution.

Revision ID: 0005_durable_execution
Revises: 0004_datasets_suites
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_durable_execution"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0004_datasets_suites"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

_RUN_STATES = "'CREATED', 'VALIDATING', 'QUEUING', 'RUNNING', 'AGGREGATING', 'PASSED', 'FAILED', 'CANCELLED', 'ERROR'"
_ITEM_STATES = "'PENDING', 'LEASED', 'EXECUTING', 'EVALUATING', 'COMPLETED', 'FAILED_RETRYABLE', 'FAILED_TERMINAL', 'CANCELLED'"


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("evaluation_suite_id", sa.String(length=26), nullable=False),
        sa.Column("candidate_agent_version_id", sa.String(length=26), nullable=False),
        sa.Column("baseline_agent_version_id", sa.String(length=26), nullable=True),
        sa.Column(
            "execution_mode", sa.String(length=32), nullable=False, server_default="recorded"
        ),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="CREATED"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_runs"),
        sa.CheckConstraint(f"state IN ({_RUN_STATES})", name="ck_evaluation_runs_state"),
        sa.CheckConstraint("item_count >= 0", name="ck_evaluation_runs_item_count_non_negative"),
        sa.CheckConstraint(
            "completed_count >= 0", name="ck_evaluation_runs_completed_non_negative"
        ),
        sa.CheckConstraint("failed_count >= 0", name="ck_evaluation_runs_failed_non_negative"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_evaluation_runs_project", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_suite_id"],
            ["evaluation_suites.id"],
            name="fk_evaluation_runs_suite",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_agent_version_id"],
            ["agent_versions.id"],
            name="fk_evaluation_runs_candidate_agent_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_agent_version_id"],
            ["agent_versions.id"],
            name="fk_evaluation_runs_baseline_agent_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_evaluation_runs_created_by",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "project_id", "idempotency_key", name="uq_evaluation_runs_project_idempotency_key"
        ),
    )
    op.create_index("ix_evaluation_runs_project_id", "evaluation_runs", ["project_id"])
    op.create_index(
        "ix_evaluation_runs_state_created_at", "evaluation_runs", ["state", "created_at"]
    )

    op.create_table(
        "run_items",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("partition", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "checkpoint",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id", name="pk_run_items"),
        sa.CheckConstraint(f"state IN ({_ITEM_STATES})", name="ck_run_items_state"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_run_items_attempt_count_non_negative"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_run_items_max_attempts_positive"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["evaluation_runs.id"], name="fk_run_items_run", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("run_id", "item_index", name="uq_run_items_run_index"),
    )
    op.create_index("ix_run_items_run_id", "run_items", ["run_id"])
    op.create_index(
        "ix_run_items_state_lease_expires_at", "run_items", ["state", "lease_expires_at"]
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=26), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
    )
    op.create_index(
        "ix_outbox_events_published_at_created_at",
        "outbox_events",
        ["published_at", "created_at"],
    )
    op.create_index(
        "ix_outbox_events_aggregate", "outbox_events", ["aggregate_type", "aggregate_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_aggregate", table_name="outbox_events")
    op.drop_index("ix_outbox_events_published_at_created_at", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_run_items_state_lease_expires_at", table_name="run_items")
    op.drop_index("ix_run_items_run_id", table_name="run_items")
    op.drop_table("run_items")
    op.drop_index("ix_evaluation_runs_state_created_at", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_project_id", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
