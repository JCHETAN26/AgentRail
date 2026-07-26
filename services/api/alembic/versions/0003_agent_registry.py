"""Agent registry and immutable versions.

Revision ID: 0003_agent_registry
Revises: 0002_identity
Create Date: 2026-07-26

Adds project-scoped agent definitions and immutable agent versions. Versions
carry a canonical content digest and have no update/delete API in application
code; the database enforces unique version numbers and content digests per
agent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_agent_registry"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0002_identity"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]


def upgrade() -> None:
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_definitions"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_agent_definitions_project", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_agent_definitions_created_by",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("project_id", "slug", name="uq_agent_definitions_project_slug"),
    )
    op.create_index("ix_agent_definitions_project_id", "agent_definitions", ["project_id"])

    op.create_table(
        "agent_versions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("agent_id", sa.String(length=26), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "graph_spec",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "prompt_bundle",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "model_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "tool_contracts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "policy_bundle",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_commit", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_versions"),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_definitions.id"],
            name="fk_agent_versions_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_agent_versions_created_by", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
        sa.UniqueConstraint("agent_id", "content_digest", name="uq_agent_versions_agent_digest"),
    )
    op.create_index("ix_agent_versions_agent_id", "agent_versions", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_versions_agent_id", table_name="agent_versions")
    op.drop_table("agent_versions")
    op.drop_index("ix_agent_definitions_project_id", table_name="agent_definitions")
    op.drop_table("agent_definitions")
