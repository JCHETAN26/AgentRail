"""Identity and tenancy tables.

Every tenant-owned row is scoped to an organisation, directly or through a
project. That is the structural half of tenant isolation; the query half is
enforced by the service layer, and both are covered by the cross-tenant tests.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentrail_core.db import Base
from agentrail_core.identity.roles import Role

_ROLES = ", ".join(f"'{role.value}'" for role in Role)


class User(Base):
    """A human. Authentication is delegated; no password is ever stored."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("auth_provider", "provider_subject", name="uq_users_provider_subject"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Which provider authenticated this user ("dev" or "github").
    auth_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The provider's own stable identifier for the user. Never the email, which
    #: providers allow to change.
    provider_subject: Mapped[str] = mapped_column(String(200), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Organisation(Base):
    __tablename__ = "organisations"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: URL-safe identifier, unique across the deployment.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Membership(Base):
    """A user's role in an organisation. The join table tenancy hangs from."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "organisation_id", name="uq_memberships_user_organisation"),
        CheckConstraint(f"role IN ({_ROLES})", name="ck_memberships_role"),
        Index("ix_memberships_organisation_id", "organisation_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organisation_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[Role] = mapped_column(String(16), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("organisation_id", "slug", name="uq_projects_organisation_slug"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Session(Base):
    """A browser session. Only the token's one-way digest is stored."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Set on sign-out. A revoked session is never reusable.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiKey(Base):
    """A scoped credential for automation. Stored only as a hash."""

    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint(f"role IN ({_ROLES})", name="ck_api_keys_role"),
        Index("ix_api_keys_organisation_id", "organisation_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    #: Public, indexed half of the presented token. Lookup key; not a secret.
    key_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    organisation_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The key cannot exceed this role, and is further narrowed by `scopes`.
    role: Mapped[Role] = mapped_column(String(16), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """An append-only record of a security-relevant action.

    Never updated and never deleted by application code. Phase 13 adds
    retention; until then the table only grows.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_organisation_created", "organisation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(String(26), nullable=False)
    #: "user", "api_key", or "system".
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    #: Stable verb, e.g. "api_key.created". Appears verbatim in exports.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Non-sensitive context only. Redacted before it is written.
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
