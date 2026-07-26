"""Shared platform primitives used by every AgentRail Python service.

This package contains no HTTP routing, no product domain logic and no
model-provider code. It exists so that the API, the worker and the CloudOps
sandbox share exactly one implementation of configuration parsing, structured
logging, request correlation and infrastructure clients.

``agentrail_core.jobs`` is the one exception: the platform job table and its
state machine are shared substrate written by the API and executed by the
worker, so neither service can own them.
"""

from agentrail_core.correlation import (
    CorrelationContext,
    current_context,
    new_correlation_id,
    parse_traceparent,
    render_traceparent,
)
from agentrail_core.errors import PlatformError, ProblemDetail
from agentrail_core.ids import new_sortable_id
from agentrail_core.logging import configure_logging, get_logger
from agentrail_core.settings import CoreSettings, Environment

__all__ = [
    "CoreSettings",
    "CorrelationContext",
    "Environment",
    "PlatformError",
    "ProblemDetail",
    "configure_logging",
    "current_context",
    "get_logger",
    "new_correlation_id",
    "new_sortable_id",
    "parse_traceparent",
    "render_traceparent",
]
