"""Roles, permissions and the authorisation decision function.

Pure domain logic: no database, no HTTP, no framework. Authorisation is decided
here and nowhere else, so a new endpoint cannot invent its own rule and a review
only has to check that the endpoint names the right permission.

The two principal kinds — a signed-in human and a CI service account holding an
API key — are deliberately unified behind :class:`Principal`, so every route
performs one check regardless of who is calling.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """A member's role within one organisation."""

    OWNER = "owner"
    ADMIN = "admin"
    DEVELOPER = "developer"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class Permission(StrEnum):
    """A single authorisable action. Values are stable and appear in audit logs."""

    ORGANISATION_READ = "organisation:read"
    ORGANISATION_UPDATE = "organisation:update"
    MEMBER_READ = "member:read"
    MEMBER_MANAGE = "member:manage"
    API_KEY_READ = "api_key:read"
    API_KEY_MANAGE = "api_key:manage"
    PROJECT_READ = "project:read"
    PROJECT_CREATE = "project:create"
    PROJECT_UPDATE = "project:update"
    JOB_READ = "job:read"
    JOB_CREATE = "job:create"
    AUDIT_READ = "audit:read"


_VIEWER: frozenset[Permission] = frozenset(
    {
        Permission.ORGANISATION_READ,
        Permission.MEMBER_READ,
        Permission.PROJECT_READ,
        Permission.JOB_READ,
    }
)

#: A reviewer approves high-risk tool calls (Phase 10). Until then the role
#: exists so memberships and audit records do not need migrating later, and it
#: reads exactly like a viewer.
_REVIEWER: frozenset[Permission] = _VIEWER | frozenset({Permission.AUDIT_READ})

_DEVELOPER: frozenset[Permission] = _REVIEWER | frozenset(
    {
        Permission.PROJECT_CREATE,
        Permission.PROJECT_UPDATE,
        Permission.JOB_CREATE,
        Permission.API_KEY_READ,
    }
)

_ADMIN: frozenset[Permission] = _DEVELOPER | frozenset(
    {
        Permission.MEMBER_MANAGE,
        Permission.API_KEY_MANAGE,
        Permission.ORGANISATION_UPDATE,
    }
)

#: An owner is an admin today. The distinction becomes load-bearing when
#: organisation deletion and billing exist; keeping it now avoids a migration.
_OWNER: frozenset[Permission] = _ADMIN

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.REVIEWER: _REVIEWER,
    Role.DEVELOPER: _DEVELOPER,
    Role.ADMIN: _ADMIN,
    Role.OWNER: _OWNER,
}


class PrincipalKind(StrEnum):
    USER = "user"
    API_KEY = "api_key"


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making a request, and what they are allowed to do.

    ``scopes`` applies only to API keys. A key can never exceed the permissions
    of its role, so the effective permission set is the *intersection* of the
    role's permissions and the key's scopes. A stolen key is therefore bounded
    twice.
    """

    kind: PrincipalKind
    id: str
    organisation_id: str
    role: Role
    scopes: frozenset[Permission] | None = None
    display_name: str | None = None

    @property
    def permissions(self) -> frozenset[Permission]:
        granted = ROLE_PERMISSIONS[self.role]
        if self.scopes is None:
            return granted
        return granted & self.scopes

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions

    @property
    def is_service_account(self) -> bool:
        return self.kind is PrincipalKind.API_KEY


class AuthorisationError(Exception):
    """Raised when a principal may not perform an action.

    Carries no detail about *why*: telling an unauthorised caller which
    organisation exists, or which permission they lack, is itself a disclosure.
    """

    def __init__(self, permission: Permission, organisation_id: str) -> None:
        super().__init__(f"Not permitted: {permission}")
        self.permission = permission
        self.organisation_id = organisation_id


def authorize(principal: Principal, permission: Permission, *, organisation_id: str) -> None:
    """Raise :class:`AuthorisationError` unless ``principal`` may act.

    Two conditions, both required:

    1. the principal belongs to ``organisation_id`` — this is the tenancy check;
    2. the principal's effective permissions include ``permission``.

    Tenancy is checked first and fails identically to a missing permission, so a
    caller cannot distinguish "that organisation is not yours" from "you lack
    that permission" — or probe for which organisations exist.
    """
    if principal.organisation_id != organisation_id:
        raise AuthorisationError(permission, organisation_id)
    if not principal.can(permission):
        raise AuthorisationError(permission, organisation_id)
