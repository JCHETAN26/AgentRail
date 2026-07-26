"""Identity, tenancy and audit; scope jobs to a project.

Revision ID: 0002_identity
Revises: 0001_create_jobs
Create Date: 2026-07-26

Adds users, organisations, memberships, projects, sessions, API keys and audit
events, then retrofits tenancy onto the existing ``jobs`` table.

The jobs retrofit is the interesting part. ``project_id`` must end up NOT NULL,
but rows already exist, so it is added nullable, backfilled against a synthetic
"legacy" organisation and project, and only then constrained. The identifiers
for those two rows are hard-coded rather than generated, so the migration is
deterministic and re-runnable against any database.

``idempotency_key`` also moves from globally unique to unique per project: two
tenants must be able to use the same key without colliding, and a 409 must not
be usable to probe for another tenant's keys.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_identity"
down_revision: str | None = "0001_create_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = "'owner', 'admin', 'developer', 'reviewer', 'viewer'"

#: Fixed identifiers for the rows that adopt pre-tenancy jobs. Valid Crockford
#: base32, so they satisfy the same shape as generated ULIDs.
LEGACY_ORGANISATION_ID = "00000000000000000000LEGACY"
LEGACY_PROJECT_ID = "0000000000000000000LEGACYP"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("auth_provider", sa.String(length=32), nullable=False),
        sa.Column("provider_subject", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("auth_provider", "provider_subject", name="uq_users_provider_subject"),
    )

    op.create_table(
        "organisations",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_organisations"),
        sa.UniqueConstraint("slug", name="uq_organisations_slug"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_organisations_created_by", ondelete="SET NULL"
        ),
    )

    op.create_table(
        "memberships",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("organisation_id", sa.String(length=26), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name="fk_memberships_organisation",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "organisation_id", name="uq_memberships_user_organisation"),
        sa.CheckConstraint(f"role IN ({_ROLES})", name="ck_memberships_role"),
    )
    op.create_index("ix_memberships_organisation_id", "memberships", ["organisation_id"])

    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("organisation_id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name="fk_projects_organisation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_projects_created_by", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("organisation_id", "slug", name="uq_projects_organisation_slug"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_sessions_user", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("organisation_id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.String(length=26), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
            name="fk_api_keys_organisation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_api_keys_created_by", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("key_id", name="uq_api_keys_key_id"),
        sa.CheckConstraint(f"role IN ({_ROLES})", name="ck_api_keys_role"),
    )
    op.create_index("ix_api_keys_organisation_id", "api_keys", ["organisation_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("organisation_id", sa.String(length=26), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=26), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index(
        "ix_audit_events_organisation_created", "audit_events", ["organisation_id", "created_at"]
    )

    # --- Retrofit tenancy onto jobs -----------------------------------------
    op.add_column("jobs", sa.Column("project_id", sa.String(length=26), nullable=True))

    # Only create the adoption rows if there is something to adopt, so a fresh
    # database does not gain a phantom organisation.
    op.execute(
        sa.text(
            f"""
            INSERT INTO organisations (id, name, slug)
            SELECT '{LEGACY_ORGANISATION_ID}', 'Legacy', 'legacy'
            WHERE EXISTS (SELECT 1 FROM jobs)
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO projects (id, organisation_id, name, slug)
            SELECT '{LEGACY_PROJECT_ID}', '{LEGACY_ORGANISATION_ID}', 'Legacy', 'legacy'
            WHERE EXISTS (SELECT 1 FROM jobs)
            """
        )
    )
    op.execute(
        sa.text(
            f"UPDATE jobs SET project_id = '{LEGACY_PROJECT_ID}' WHERE project_id IS NULL"
        )
    )

    op.alter_column("jobs", "project_id", nullable=False)
    op.create_foreign_key(
        "fk_jobs_project", "jobs", "projects", ["project_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])

    op.drop_constraint("uq_jobs_idempotency_key", "jobs", type_="unique")
    op.create_unique_constraint(
        "uq_jobs_project_idempotency_key", "jobs", ["project_id", "idempotency_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_jobs_project_idempotency_key", "jobs", type_="unique")
    # Restoring a global unique constraint can fail if two projects legitimately
    # used the same key. Drop the duplicates' keys first — they are only a retry
    # optimisation, never referenced by another row.
    op.execute(
        sa.text(
            """
            UPDATE jobs SET idempotency_key = NULL
            WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY idempotency_key ORDER BY created_at, id
                    ) AS rn
                    FROM jobs WHERE idempotency_key IS NOT NULL
                ) ranked WHERE ranked.rn > 1
            )
            """
        )
    )
    op.create_unique_constraint("uq_jobs_idempotency_key", "jobs", ["idempotency_key"])

    op.drop_index("ix_jobs_project_id", table_name="jobs")
    op.drop_constraint("fk_jobs_project", "jobs", type_="foreignkey")
    op.drop_column("jobs", "project_id")

    op.drop_index("ix_audit_events_organisation_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_api_keys_organisation_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("projects")
    op.drop_index("ix_memberships_organisation_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("organisations")
    op.drop_table("users")
