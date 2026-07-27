"""Evaluators and comparison reports.

Revision ID: 0007_evaluators_comparison
Revises: 0006_trajectories
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_evaluators_comparison"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0006_trajectories"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

_EVALUATOR_KINDS = "'programmatic', 'judge'"
_RESULT_STATES = "'PASSED', 'FAILED', 'ERROR'"


def upgrade() -> None:
    op.create_table(
        "evaluator_versions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("definition_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evaluator_versions"),
        sa.CheckConstraint(f"kind IN ({_EVALUATOR_KINDS})", name="ck_evaluator_versions_kind"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_evaluator_versions_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id", "slug", "version", name="uq_evaluator_versions_slug_version"
        ),
        sa.UniqueConstraint("project_id", "definition_digest", name="uq_evaluator_versions_digest"),
    )
    op.create_index("ix_evaluator_versions_project_id", "evaluator_versions", ["project_id"])

    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("run_item_id", sa.String(length=26), nullable=False),
        sa.Column("evaluator_version_id", sa.String(length=26), nullable=True),
        sa.Column("evaluator_slug", sa.String(length=64), nullable=False),
        sa.Column("evaluator_kind", sa.String(length=32), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("partition", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_results"),
        sa.CheckConstraint(f"state IN ({_RESULT_STATES})", name="ck_evaluation_results_state"),
        sa.CheckConstraint("item_index >= 0", name="ck_evaluation_results_item_index_non_negative"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["evaluation_runs.id"], name="fk_evaluation_results_run", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_item_id"],
            ["run_items.id"],
            name="fk_evaluation_results_run_item",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evaluator_version_id"],
            ["evaluator_versions.id"],
            name="fk_evaluation_results_evaluator_version",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "run_item_id", "evaluator_slug", name="uq_evaluation_results_item_evaluator"
        ),
    )
    op.create_index("ix_evaluation_results_run_id", "evaluation_results", ["run_id"])
    op.create_index(
        "ix_evaluation_results_run_evaluator",
        "evaluation_results",
        ["run_id", "evaluator_slug"],
    )

    op.create_table(
        "comparison_reports",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("baseline_agent_version_id", sa.String(length=26), nullable=True),
        sa.Column("candidate_agent_version_id", sa.String(length=26), nullable=False),
        sa.Column("suite_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evaluator_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "category_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "regressions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "exports",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_comparison_reports"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_comparison_reports_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            name="fk_comparison_reports_run",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("run_id", name="uq_comparison_reports_run_id"),
    )
    op.create_index("ix_comparison_reports_project_id", "comparison_reports", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_comparison_reports_project_id", table_name="comparison_reports")
    op.drop_table("comparison_reports")
    op.drop_index("ix_evaluation_results_run_evaluator", table_name="evaluation_results")
    op.drop_index("ix_evaluation_results_run_id", table_name="evaluation_results")
    op.drop_table("evaluation_results")
    op.drop_index("ix_evaluator_versions_project_id", table_name="evaluator_versions")
    op.drop_table("evaluator_versions")
