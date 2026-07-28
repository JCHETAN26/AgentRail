"""Identity, tenancy and authorisation.

The authorisation decision lives in :mod:`agentrail_core.identity.roles` and is
pure — no database, no HTTP — so it can be exhaustively tested and cannot drift
per endpoint.
"""

from agentrail_core.agents import AgentDefinition, AgentVersion
from agentrail_core.datasets import Dataset, DatasetVersion, EvaluationSuite
from agentrail_core.deployments import Deployment
from agentrail_core.evaluators import ComparisonReport, EvaluationResult, EvaluatorVersion
from agentrail_core.execution import EvaluationRun, OutboxEvent, RunItem
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
from agentrail_core.trajectories import (
    Trajectory,
    TrajectoryCheckpoint,
    TrajectoryReplay,
    TrajectoryStep,
)
from agentrail_core.tribunal import (
    TribunalArgument,
    TribunalBlackboardEntry,
    TribunalFinding,
    TribunalSession,
    TribunalVerdict,
)

__all__ = [
    "ROLE_PERMISSIONS",
    "AgentDefinition",
    "AgentVersion",
    "ApiKey",
    "AuditEvent",
    "AuthorisationError",
    "ComparisonReport",
    "Dataset",
    "DatasetVersion",
    "Deployment",
    "EvaluationResult",
    "EvaluationRun",
    "EvaluationSuite",
    "EvaluatorVersion",
    "GeneratedApiKey",
    "Membership",
    "Organisation",
    "OutboxEvent",
    "Permission",
    "Principal",
    "PrincipalKind",
    "Project",
    "Role",
    "RunItem",
    "Session",
    "Trajectory",
    "TrajectoryCheckpoint",
    "TrajectoryReplay",
    "TrajectoryStep",
    "TribunalArgument",
    "TribunalBlackboardEntry",
    "TribunalFinding",
    "TribunalSession",
    "TribunalVerdict",
    "User",
    "authorize",
    "generate_api_key",
    "generate_oauth_state",
    "generate_session_token",
    "hash_session_token",
    "parse_api_key",
    "verify_secret",
]
