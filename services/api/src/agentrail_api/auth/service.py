"""Authentication: turning a credential into an actor, and an actor into a principal.

Two credential kinds are accepted and normalised to the same
:class:`~agentrail_core.identity.roles.Principal`, so no route has to know which
was used:

* an ``agentrail_session`` cookie, for a signed-in human;
* an ``Authorization: Bearer ar_...`` header, for CI and automation.

Authentication answers "who is this?". Authorisation — "may they?" — is decided
by :func:`agentrail_core.identity.roles.authorize` and never here.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.auth.providers import ExternalIdentity
from agentrail_core.db import set_tenant_context
from agentrail_core.errors import ForbiddenError, UnauthenticatedError
from agentrail_core.identity import (
    ApiKey,
    AuditEvent,
    Membership,
    Organisation,
    Permission,
    Principal,
    PrincipalKind,
    Role,
    Session,
    User,
)
from agentrail_core.identity.secrets import (
    generate_session_token,
    hash_session_token,
    parse_api_key,
    verify_secret,
)
from agentrail_core.ids import new_sortable_id

SESSION_COOKIE_NAME = "agentrail_session"
BEARER_PREFIX = "bearer "
LEGACY_ORGANISATION_ID = "01KYC7S3G00000000000000000"
_FINGERPRINT_HASH_PREFIX = "agentrail-api-key-fingerprint-v1"


@dataclass(frozen=True, slots=True)
class Actor:
    """An authenticated identity, before any organisation is chosen."""

    user: User | None = None
    api_key: ApiKey | None = None

    @property
    def is_user(self) -> bool:
        return self.user is not None

    @property
    def audit_actor(self) -> tuple[str, str | None]:
        if self.user is not None:
            return "user", self.user.id
        if self.api_key is not None:
            return "api_key", self.api_key.id
        return "system", None


async def upsert_user(session: AsyncSession, identity: ExternalIdentity) -> User:
    """Find or create the user behind a verified external identity.

    Matching is on ``(provider, subject)``, never on email: an email can be
    reassigned by the provider, and matching on it would let a new owner of an
    address inherit the original account.
    """
    existing = await session.scalar(
        select(User).where(
            User.auth_provider == identity.provider,
            User.provider_subject == identity.subject,
        )
    )
    now = datetime.now(UTC)
    if existing is not None:
        existing.email = identity.email
        existing.display_name = identity.display_name
        existing.last_seen_at = now
        return existing

    user = User(
        id=new_sortable_id(),
        email=identity.email,
        display_name=identity.display_name,
        auth_provider=identity.provider,
        provider_subject=identity.subject,
        last_seen_at=now,
    )
    session.add(user)
    await session.flush()
    return user


async def create_session(
    session: AsyncSession, user: User, *, ttl_seconds: int
) -> tuple[Session, str]:
    """Create a session, returning the row and the raw token.

    The raw token is the only value that can authenticate, and it is never
    persisted — only its digest is.
    """
    token, token_hash = generate_session_token()
    record = Session(
        id=new_sortable_id(),
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
    )
    session.add(record)
    await session.flush()
    return record, token


async def claim_legacy_organisation_if_unowned(session: AsyncSession, user: User) -> None:
    """Give the first signed-in user access to jobs adopted from Phase 0.

    Phase 0 jobs had no tenant owner. The migration places them in a deterministic
    Legacy organisation; this hook prevents that organisation from being
    permanently orphaned after upgrade.
    """

    legacy_exists = await session.scalar(
        select(Organisation.id).where(Organisation.id == LEGACY_ORGANISATION_ID)
    )
    if legacy_exists is None:
        return

    existing_owner = await session.scalar(
        select(Membership.id).where(Membership.organisation_id == LEGACY_ORGANISATION_ID)
    )
    if existing_owner is not None:
        return

    session.add(
        Membership(
            id=new_sortable_id(),
            user_id=user.id,
            organisation_id=LEGACY_ORGANISATION_ID,
            role=Role.OWNER,
        )
    )
    await session.flush()


async def revoke_session(session: AsyncSession, token: str) -> bool:
    record = await session.scalar(
        select(Session).where(Session.token_hash == hash_session_token(token))
    )
    if record is None or record.revoked_at is not None:
        return False
    record.revoked_at = datetime.now(UTC)
    return True


async def _actor_from_session_token(session: AsyncSession, token: str) -> Actor | None:
    record = await session.scalar(
        select(Session).where(Session.token_hash == hash_session_token(token))
    )
    if record is None or record.revoked_at is not None:
        return None
    if record.expires_at <= datetime.now(UTC):
        return None
    user = await session.get(User, record.user_id)
    if user is None:  # pragma: no cover - cascade makes this unreachable
        return None
    return Actor(user=user)


async def _actor_from_api_key(
    session: AsyncSession,
    token: str,
    *,
    client_host: str | None,
    user_agent: str | None,
    inactivity_anomaly_days: int,
    fingerprint_secret: str,
    correlation_id: str | None,
) -> Actor | None:
    parsed = parse_api_key(token)
    if parsed is None:
        return None
    key_id, secret = parsed

    record = await session.scalar(select(ApiKey).where(ApiKey.key_id == key_id).with_for_update())
    if record is None:
        return None
    # Verify the secret before checking status, so a revoked key and an unknown
    # key take the same path and cannot be distinguished by timing.
    if not verify_secret(secret, record.secret_hash):
        return None
    now = datetime.now(UTC)
    if record.revoked_at is not None:
        return None
    if record.expires_at is not None and record.expires_at <= now:
        return None

    await _record_api_key_usage(
        session,
        record,
        now=now,
        client_host=client_host,
        user_agent=user_agent,
        inactivity_anomaly_days=inactivity_anomaly_days,
        fingerprint_secret=fingerprint_secret,
        correlation_id=correlation_id,
    )
    await session.commit()
    return Actor(api_key=record)


async def authenticate(
    session: AsyncSession,
    *,
    cookie_token: str | None,
    authorization: str | None,
    client_host: str | None = None,
    user_agent: str | None = None,
    inactivity_anomaly_days: int = 30,
    fingerprint_secret: str | None = None,
    correlation_id: str | None = None,
) -> Actor:
    """Resolve a credential to an :class:`Actor`, or raise.

    The bearer token wins when both are present, so a CI job's explicit
    credential is never silently overridden by a stray browser cookie.
    """
    if authorization and authorization.lower().startswith(BEARER_PREFIX):
        actor = await _actor_from_api_key(
            session,
            authorization[len(BEARER_PREFIX) :].strip(),
            client_host=client_host,
            user_agent=user_agent,
            inactivity_anomaly_days=inactivity_anomaly_days,
            fingerprint_secret=fingerprint_secret or _default_fingerprint_secret(),
            correlation_id=correlation_id,
        )
        if actor is not None:
            return actor
        raise UnauthenticatedError("The API key is invalid, expired or revoked.")

    if cookie_token:
        actor = await _actor_from_session_token(session, cookie_token)
        if actor is not None:
            return actor
        raise UnauthenticatedError("Your session has expired. Sign in again.")

    raise UnauthenticatedError("Authentication is required.")


async def _record_api_key_usage(
    session: AsyncSession,
    record: ApiKey,
    *,
    now: datetime,
    client_host: str | None,
    user_agent: str | None,
    inactivity_anomaly_days: int,
    fingerprint_secret: str,
    correlation_id: str | None,
) -> None:
    previous_last_used_at = record.last_used_at
    previous_ip_hash = record.last_used_ip_hash
    previous_user_agent_hash = record.last_used_user_agent_hash
    current_ip_hash = _fingerprint_hash(client_host, secret=fingerprint_secret)
    current_user_agent_hash = _fingerprint_hash(user_agent, secret=fingerprint_secret)

    if previous_last_used_at is None:
        _add_api_key_usage_audit_event(
            session,
            record,
            action="api_key.first_used",
            context={
                "key_id": record.key_id,
                "client_ip_hash": current_ip_hash,
                "user_agent_hash": current_user_agent_hash,
            },
            correlation_id=correlation_id,
        )

    anomaly_reasons: list[str] = []
    if previous_last_used_at is not None and previous_last_used_at <= now - timedelta(
        days=inactivity_anomaly_days
    ):
        anomaly_reasons.append("inactive_key_reused")
    if (
        previous_ip_hash is not None
        and current_ip_hash is not None
        and previous_ip_hash != current_ip_hash
    ):
        anomaly_reasons.append("client_ip_changed")
    if (
        previous_user_agent_hash is not None
        and current_user_agent_hash is not None
        and previous_user_agent_hash != current_user_agent_hash
    ):
        anomaly_reasons.append("user_agent_changed")

    if anomaly_reasons:
        record.last_anomaly_at = now
        record.anomaly_count += 1
        _add_api_key_usage_audit_event(
            session,
            record,
            action="api_key.anomaly_detected",
            context={
                "key_id": record.key_id,
                "reasons": anomaly_reasons,
                "previous_last_used_at": (
                    previous_last_used_at.isoformat() if previous_last_used_at else None
                ),
                "client_ip_hash": current_ip_hash,
                "user_agent_hash": current_user_agent_hash,
                "inactivity_threshold_days": inactivity_anomaly_days,
            },
            correlation_id=correlation_id,
        )

    record.last_used_at = now
    if current_ip_hash is not None:
        record.last_used_ip_hash = current_ip_hash
    if current_user_agent_hash is not None:
        record.last_used_user_agent_hash = current_user_agent_hash


def _fingerprint_hash(value: str | None, *, secret: str) -> str | None:
    if value is None or not value.strip():
        return None
    return hmac.digest(
        secret.encode(),
        f"{_FINGERPRINT_HASH_PREFIX}:{value.strip()}".encode(),
        "sha256",
    ).hex()


def _default_fingerprint_secret() -> str:
    return "agentrail-local-api-key-fingerprint:default"


def _add_api_key_usage_audit_event(
    session: AsyncSession,
    record: ApiKey,
    *,
    action: str,
    context: dict[str, object],
    correlation_id: str | None,
) -> None:
    session.add(
        AuditEvent(
            id=new_sortable_id(),
            organisation_id=record.organisation_id,
            actor_type="api_key",
            actor_id=record.id,
            action=action,
            target_type="api_key",
            target_id=record.id,
            context={key: value for key, value in context.items() if value is not None},
            correlation_id=correlation_id,
        )
    )


def _scopes_from_strings(values: list[str]) -> frozenset[Permission] | None:
    """Interpret stored scope strings.

    An empty list means "no narrowing" — the key gets its role's permissions.
    Unknown strings are dropped rather than trusted, so a scope removed from the
    codebase cannot keep granting access.
    """
    if not values:
        return None
    known = {permission.value: permission for permission in Permission}
    return frozenset(known[value] for value in values if value in known)


async def principal_for_organisation(
    session: AsyncSession, actor: Actor, organisation_id: str
) -> Principal:
    """Build the principal for ``actor`` acting in ``organisation_id``.

    Raises :class:`ForbiddenError` when the actor has no membership there. The
    same error is raised whether the organisation does not exist or merely is
    not theirs — otherwise the response distinguishes the two and becomes an
    enumeration oracle.
    """
    if actor.api_key is not None:
        key = actor.api_key
        if key.organisation_id != organisation_id:
            raise ForbiddenError()
        await set_tenant_context(session, key.organisation_id)
        return Principal(
            kind=PrincipalKind.API_KEY,
            id=key.id,
            organisation_id=key.organisation_id,
            role=Role(key.role),
            scopes=_scopes_from_strings(list(key.scopes)),
            display_name=key.name,
        )

    if actor.user is None:  # pragma: no cover - Actor always has one of the two
        raise ForbiddenError()

    membership = await session.scalar(
        select(Membership).where(
            Membership.user_id == actor.user.id,
            Membership.organisation_id == organisation_id,
        )
    )
    if membership is None:
        raise ForbiddenError()

    await set_tenant_context(session, organisation_id)
    return Principal(
        kind=PrincipalKind.USER,
        id=actor.user.id,
        organisation_id=organisation_id,
        role=Role(membership.role),
        display_name=actor.user.display_name,
    )
