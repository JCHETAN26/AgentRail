"""The platform job record.

The API is the only writer of new jobs; the worker is the only executor. Both
processes need the same table definition and the same transition rules, so they
live here rather than inside either service. Alembic migrations for this table
are owned by ``services/api``.
"""

from agentrail_core.jobs.models import Job
from agentrail_core.jobs.state import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    IllegalStateTransitionError,
    JobState,
    assert_transition,
    can_transition,
    is_terminal,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATES",
    "IllegalStateTransitionError",
    "Job",
    "JobState",
    "assert_transition",
    "can_transition",
    "is_terminal",
]
