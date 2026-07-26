"""Datasets, dataset versions and evaluation suites.

Revision ID: 0004_datasets_suites
Revises: 0003_agent_registry
Create Date: 2026-07-26

Adds project-scoped datasets, immutable dataset versions with validation
reports, and evaluation suites that can be frozen.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_datasets_suites"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0003_agent_registry"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_datasets"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_datasets_project", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_datasets_created_by", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("project_id", "slug", name="uq_datasets_project_slug"),
    )
    op.create_index("ix_datasets_project_id", "datasets", ["project_id"])

    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("dataset_id", sa.String(length=26), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("storage_uri", sa.String(length=512), nullable=False),
        sa.Column("input_format", sa.String(length=16), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column(
            "schema",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "validation_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "partition_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_versions"),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"], name="fk_dataset_versions_dataset", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_dataset_versions_created_by",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("dataset_id", "version", name="uq_dataset_versions_dataset_version"),
        sa.UniqueConstraint(
            "dataset_id", "content_digest", name="uq_dataset_versions_dataset_digest"
        ),
    )
    op.create_index("ix_dataset_versions_dataset_id", "dataset_versions", ["dataset_id"])

    op.create_table(
        "evaluation_suites",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("dataset_version_id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column(
            "evaluators",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "fault_profiles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "preview",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_suites"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_evaluation_suites_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name="fk_evaluation_suites_dataset_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_evaluation_suites_created_by",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("project_id", "slug", name="uq_evaluation_suites_project_slug"),
    )
    op.create_index("ix_evaluation_suites_project_id", "evaluation_suites", ["project_id"])
    op.create_index(
        "ix_evaluation_suites_dataset_version_id", "evaluation_suites", ["dataset_version_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_suites_dataset_version_id", table_name="evaluation_suites")
    op.drop_index("ix_evaluation_suites_project_id", table_name="evaluation_suites")
    op.drop_table("evaluation_suites")
    op.drop_index("ix_dataset_versions_dataset_id", table_name="dataset_versions")
    op.drop_table("dataset_versions")
    op.drop_index("ix_datasets_project_id", table_name="datasets")
    op.drop_table("datasets")
