"""Identity, tenancy and authorisation.

The authorisation decision lives in :mod:`agentrail_core.identity.roles` and is
pure — no database, no HTTP — so it can be exhaustively tested and cannot drift
per endpoint.
"""

from agentrail_core.identity.models import (
    ApiKey,
    AuditEvent,
    Membership,
    Organisation,
    Project,
    Session,
    User,
)
from agentrail_core.identity.roles import (
    ROLE_PERMISSIONS,
    AuthorisationError,
    Permission,
    Principal,
    PrincipalKind,
    Role,
    authorize,
)
from agentrail_core.identity.secrets import (
    GeneratedApiKey,
    generate_api_key,
    generate_oauth_state,
    generate_session_token,
    hash_session_token,
    parse_api_key,
    verify_secret,
)

__all__ = [
    "ROLE_PERMISSIONS",
    "ApiKey",
    "AuditEvent",
    "AuthorisationError",
    "GeneratedApiKey",
    "Membership",
    "Organisation",
    "Permission",
    "Principal",
    "PrincipalKind",
    "Project",
    "Role",
    "Session",
    "User",
    "authorize",
    "generate_api_key",
    "generate_oauth_state",
    "generate_session_token",
    "hash_session_token",
    "parse_api_key",
    "verify_secret",
]
