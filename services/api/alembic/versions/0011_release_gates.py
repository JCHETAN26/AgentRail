"""Release policies, gate evaluations and pull-request linkage on runs.

Revision ID: 0011_release_gates
Revises: 0010_policy_and_approval
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_release_gates"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0010_policy_and_approval"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]


def upgrade() -> None:
    op.create_table(
        "release_policies",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("definition_digest", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_release_policies"),
        sa.CheckConstraint("version >= 1", name="ck_release_policies_version_positive"),
        # Immutable, like agent versions: when a gate blocks a pull request,
        # "which rules was it judged against?" must have exactly one answer.
        sa.UniqueConstraint(
            "project_id", "slug", "version", name="uq_release_policies_slug_version"
        ),
        sa.UniqueConstraint(
            "project_id", "definition_digest", name="uq_release_policies_project_digest"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_release_policies_project", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_release_policies_created_by", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_release_policies_project_id", "release_policies", ["project_id"])

    op.create_table(
        "gate_evaluations",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("release_policy_id", sa.String(length=26), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column(
            "violations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("summary", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("head_sha", sa.String(length=64), nullable=True),
        sa.Column("check_run", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_gate_evaluations"),
        # The decision is a pure function of the run and the policy, so asking
        # twice returns the recorded answer rather than computing a second one.
        sa.UniqueConstraint("run_id", "release_policy_id", name="uq_gate_evaluations_run_policy"),
        sa.CheckConstraint("outcome IN ('passed', 'blocked')", name="ck_gate_evaluations_outcome"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_gate_evaluations_project", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            name="fk_gate_evaluations_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["release_policy_id"],
            ["release_policies.id"],
            name="fk_gate_evaluations_policy",
            # RESTRICT: deleting a policy must not orphan the record of what a
            # pull request was judged against.
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_gate_evaluations_project_id", "gate_evaluations", ["project_id"])
    op.create_index("ix_gate_evaluations_head_sha", "gate_evaluations", ["head_sha"])

    # Pull-request provenance on the run itself. All nullable — a run started
    # from the console has no pull request, and the gate works without one.
    op.add_column(
        "evaluation_runs", sa.Column("github_owner", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "evaluation_runs", sa.Column("github_repository", sa.String(length=200), nullable=True)
    )
    op.add_column("evaluation_runs", sa.Column("github_pull_number", sa.Integer(), nullable=True))
    op.add_column("evaluation_runs", sa.Column("github_head_sha", sa.String(length=64), nullable=True))
    # The lookup superseded-run cancellation makes on every pull-request push.
    op.create_index(
        "ix_evaluation_runs_pull_request",
        "evaluation_runs",
        ["github_owner", "github_repository", "github_pull_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_runs_pull_request", table_name="evaluation_runs")
    op.drop_column("evaluation_runs", "github_head_sha")
    op.drop_column("evaluation_runs", "github_pull_number")
    op.drop_column("evaluation_runs", "github_repository")
    op.drop_column("evaluation_runs", "github_owner")

    op.drop_index("ix_gate_evaluations_head_sha", table_name="gate_evaluations")
    op.drop_index("ix_gate_evaluations_project_id", table_name="gate_evaluations")
    op.drop_table("gate_evaluations")

    op.drop_index("ix_release_policies_project_id", table_name="release_policies")
    op.drop_table("release_policies")
