"""Organisation, project, membership and API-key use cases, plus the audit log.

Every read here takes the organisation or project scope as an explicit argument
rather than inferring it. A query that is not scoped is a tenancy bug, and
making the scope a required parameter is what keeps that mistake from being
easy to make.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.auth.service import Actor, principal_for_organisation
from agentrail_api.settings import ApiSettings
from agentrail_core.correlation import current_context
from agentrail_core.errors import ConflictError, ForbiddenError, ValidationFailedError
from agentrail_core.identity import (
    ROLE_PERMISSIONS,
    ApiKey,
    AuditEvent,
    Membership,
    Organisation,
    Permission,
    Principal,
    Project,
    Role,
    User,
    authorize,
    generate_api_key,
)
from agentrail_core.ids import new_sortable_id
from agentrail_core.logging import redact

MAX_PAGE_SIZE = 100
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = _SLUG_STRIP.sub("-", value.strip().lower()).strip("-")[:64]
    if not slug:
        raise ValidationFailedError(
            "Name must contain at least one letter or digit.", details={"field": "name"}
        )
    return slug


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


async def record_audit(
    session: AsyncSession,
    *,
    organisation_id: str,
    actor: Actor,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    context: dict[str, object] | None = None,
) -> AuditEvent:
    """Append an audit event.

    Context is passed through the same redaction the logger uses, so a caller
    cannot accidentally persist a secret into a table that is never deleted.
    """
    actor_type, actor_id = actor.audit_actor
    correlation = current_context()
    event = AuditEvent(
        id=new_sortable_id(),
        organisation_id=organisation_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        context=redact(context or {}),
        correlation_id=correlation.correlation_id if correlation else None,
    )
    session.add(event)
    return event


# ---------------------------------------------------------------------------
# Organisations and membership
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrganisationMembership:
    organisation: Organisation
    role: Role


async def create_organisation(
    session: AsyncSession, actor: Actor, *, name: str
) -> tuple[Organisation, Project]:
    """Create an organisation, its owner membership and a default project.

    Only a signed-in human may do this. An API key is scoped to exactly one
    organisation by construction, so letting it create another would break the
    containment that makes a leaked key survivable.
    """
    if actor.user is None:
        raise ForbiddenError()

    slug = slugify(name)
    if await session.scalar(select(Organisation.id).where(Organisation.slug == slug)):
        raise ConflictError(
            "An organisation with that name already exists.", details={"slug": slug}
        )

    organisation = Organisation(
        id=new_sortable_id(), name=name.strip(), slug=slug, created_by=actor.user.id
    )
    session.add(organisation)
    # Flushed on its own first: the membership and project both carry a foreign
    # key to it, and these models declare no ORM relationships, so SQLAlchemy has
    # nothing to infer an insert order from.
    await session.flush()

    session.add(
        Membership(
            id=new_sortable_id(),
            user_id=actor.user.id,
            organisation_id=organisation.id,
            role=Role.OWNER,
        )
    )
    project = Project(
        id=new_sortable_id(),
        organisation_id=organisation.id,
        name="Default",
        slug="default",
        created_by=actor.user.id,
    )
    session.add(project)
    await session.flush()

    await record_audit(
        session,
        organisation_id=organisation.id,
        actor=actor,
        action="organisation.created",
        target_type="organisation",
        target_id=organisation.id,
        context={"slug": slug},
    )
    return organisation, project


async def list_organisations_for_actor(
    session: AsyncSession, actor: Actor
) -> list[OrganisationMembership]:
    """Every organisation the actor can see — and only those."""
    if actor.api_key is not None:
        organisation = await session.get(Organisation, actor.api_key.organisation_id)
        if organisation is None:  # pragma: no cover - cascade prevents this
            return []
        return [OrganisationMembership(organisation, Role(actor.api_key.role))]

    if actor.user is None:  # pragma: no cover
        return []

    rows = await session.execute(
        select(Organisation, Membership.role)
        .join(Membership, Membership.organisation_id == Organisation.id)
        .where(Membership.user_id == actor.user.id)
        .order_by(Organisation.id)
    )
    return [OrganisationMembership(org, Role(role)) for org, role in rows.all()]


async def get_organisation(session: AsyncSession, principal: Principal) -> Organisation:
    """Load the organisation the principal is already scoped to."""
    organisation = await session.get(Organisation, principal.organisation_id)
    if organisation is None:  # pragma: no cover - principal implies existence
        raise ForbiddenError()
    return organisation


async def get_user(session: AsyncSession, user_id: str) -> User:
    user = await session.get(User, user_id)
    if user is None:  # pragma: no cover - callers hold a membership row
        raise ForbiddenError()
    return user


async def list_members(
    session: AsyncSession, principal: Principal
) -> list[tuple[User, Membership]]:
    authorize(principal, Permission.MEMBER_READ, organisation_id=principal.organisation_id)
    rows = await session.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.organisation_id == principal.organisation_id)
        .order_by(Membership.id)
    )
    return [(user, membership) for user, membership in rows.all()]


async def add_member(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    email: str,
    role: Role,
) -> Membership:
    authorize(principal, Permission.MEMBER_MANAGE, organisation_id=principal.organisation_id)

    user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        # Invitations for users who have never signed in are Phase 18 work.
        raise ValidationFailedError(
            "That person has not signed in to AgentRail yet.", details={"field": "email"}
        )

    existing = await session.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organisation_id == principal.organisation_id,
        )
    )
    if existing is not None:
        existing.role = role
        membership = existing
    else:
        membership = Membership(
            id=new_sortable_id(),
            user_id=user.id,
            organisation_id=principal.organisation_id,
            role=role,
        )
        session.add(membership)
    await session.flush()

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="member.granted",
        target_type="user",
        target_id=user.id,
        context={"role": role.value},
    )
    return membership


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


async def create_project(
    session: AsyncSession, actor: Actor, principal: Principal, *, name: str
) -> Project:
    authorize(principal, Permission.PROJECT_CREATE, organisation_id=principal.organisation_id)

    slug = slugify(name)
    duplicate = await session.scalar(
        select(Project.id).where(
            Project.organisation_id == principal.organisation_id, Project.slug == slug
        )
    )
    if duplicate:
        raise ConflictError("A project with that name already exists.", details={"slug": slug})

    project = Project(
        id=new_sortable_id(),
        organisation_id=principal.organisation_id,
        name=name.strip(),
        slug=slug,
        created_by=actor.user.id if actor.user else None,
    )
    session.add(project)
    await session.flush()

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="project.created",
        target_type="project",
        target_id=project.id,
        context={"slug": slug},
    )
    return project


async def list_projects(session: AsyncSession, principal: Principal) -> list[Project]:
    authorize(principal, Permission.PROJECT_READ, organisation_id=principal.organisation_id)
    rows = await session.scalars(
        select(Project)
        .where(Project.organisation_id == principal.organisation_id)
        .order_by(Project.id)
    )
    return list(rows.all())


async def resolve_project(
    session: AsyncSession, actor: Actor, project_id: str
) -> tuple[Principal, Project]:
    """Load a project and the actor's principal within its organisation.

    A project the actor cannot see raises :class:`ForbiddenError` — the same
    error as one that does not exist. Returning 404 for the latter would confirm
    which identifiers are real.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise ForbiddenError()
    principal = await principal_for_organisation(session, actor, project.organisation_id)
    return principal, project


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    record: ApiKey
    #: Shown once, at creation, and never recoverable afterwards.
    token: str


async def create_api_key(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    name: str,
    role: Role,
    scopes: list[Permission],
    expires_at: datetime | None = None,
) -> IssuedApiKey:
    authorize(principal, Permission.API_KEY_MANAGE, organisation_id=principal.organisation_id)

    # A key must never out-rank the principal minting it, or an admin could
    # bootstrap an owner credential and a leaked key could escalate.
    if not ROLE_PERMISSIONS[role] <= principal.permissions:
        raise ForbiddenError()

    generated = generate_api_key()
    record = ApiKey(
        id=new_sortable_id(),
        key_id=generated.key_id,
        secret_hash=generated.secret_hash,
        organisation_id=principal.organisation_id,
        name=name.strip()[:200],
        role=role,
        scopes=[scope.value for scope in scopes],
        created_by=actor.user.id if actor.user else None,
        expires_at=expires_at,
    )
    session.add(record)
    await session.flush()

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="api_key.created",
        target_type="api_key",
        target_id=record.id,
        # The token itself is never recorded — only its public identifier.
        context={"key_id": record.key_id, "role": role.value},
    )
    return IssuedApiKey(record=record, token=generated.token)


async def list_api_keys(session: AsyncSession, principal: Principal) -> list[ApiKey]:
    authorize(principal, Permission.API_KEY_READ, organisation_id=principal.organisation_id)
    rows = await session.scalars(
        select(ApiKey)
        .where(ApiKey.organisation_id == principal.organisation_id)
        .order_by(ApiKey.id.desc())
    )
    return list(rows.all())


async def revoke_api_key(
    session: AsyncSession, actor: Actor, principal: Principal, *, key_id: str
) -> ApiKey:
    authorize(principal, Permission.API_KEY_MANAGE, organisation_id=principal.organisation_id)

    record = await session.scalar(
        select(ApiKey).where(
            ApiKey.id == key_id, ApiKey.organisation_id == principal.organisation_id
        )
    )
    if record is None:
        raise ForbiddenError()
    if record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="api_key.revoked",
        target_type="api_key",
        target_id=record.id,
        context={"key_id": record.key_id},
    )
    return record


async def rotate_api_key(
    session: AsyncSession, actor: Actor, principal: Principal, *, key_id: str
) -> IssuedApiKey:
    authorize(principal, Permission.API_KEY_MANAGE, organisation_id=principal.organisation_id)

    record = await session.scalar(
        select(ApiKey).where(
            ApiKey.id == key_id, ApiKey.organisation_id == principal.organisation_id
        )
    )
    if record is None or record.revoked_at is not None:
        raise ForbiddenError()

    previous_key_id = record.key_id
    generated = generate_api_key()
    record.key_id = generated.key_id
    record.secret_hash = generated.secret_hash
    record.last_used_at = None
    await session.flush()

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="api_key.rotated",
        target_type="api_key",
        target_id=record.id,
        context={"previous_key_id": previous_key_id, "key_id": record.key_id},
    )
    return IssuedApiKey(record=record, token=generated.token)


async def list_audit_events(
    session: AsyncSession, principal: Principal, *, limit: int = 50
) -> list[AuditEvent]:
    authorize(principal, Permission.AUDIT_READ, organisation_id=principal.organisation_id)
    bounded = max(1, min(limit, MAX_PAGE_SIZE))
    rows = await session.scalars(
        select(AuditEvent)
        .where(AuditEvent.organisation_id == principal.organisation_id)
        .order_by(AuditEvent.id.desc())
        .limit(bounded)
    )
    return list(rows.all())


async def prune_expired_audit_events(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    settings: ApiSettings,
    *,
    now: datetime,
) -> tuple[datetime, int]:
    """Delete audit rows older than the configured organisation retention window."""
    authorize(principal, Permission.AUDIT_MANAGE, organisation_id=principal.organisation_id)
    cutoff = now - timedelta(days=settings.audit_event_retention_days)
    result = await session.execute(
        delete(AuditEvent).where(
            AuditEvent.organisation_id == principal.organisation_id,
            AuditEvent.created_at < cutoff,
        )
    )
    deleted_count = int(result.rowcount or 0)
    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="audit_events.pruned",
        target_type="organisation",
        target_id=principal.organisation_id,
        context={
            "retention_days": settings.audit_event_retention_days,
            "cutoff": cutoff.isoformat(),
            "deleted_count": deleted_count,
        },
    )
    return cutoff, deleted_count


async def count_jobs_in_organisation(session: AsyncSession, organisation_id: str) -> int:
    """Used by tests and the console summary; scoped like everything else."""
    from agentrail_core.jobs import Job

    total = await session.scalar(
        select(func.count(Job.id))
        .join(Project, Project.id == Job.project_id)
        .where(Project.organisation_id == organisation_id)
    )
    return int(total or 0)
