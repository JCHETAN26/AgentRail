"""Shared types and constants for the worker test suite.

Given a uniquely qualified module name (rather than ``support`` or a relative
import from ``conftest``) so that pytest's path-based collection cannot confuse
it with a same-named helper in another service's test directory.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from agentrail_core.jobs import JobState

WORKER_ID = "worker-under-test"


class JobFactory(Protocol):
    """Inserts a job row directly, bypassing the API."""

    async def __call__(
        self,
        *,
        message: str = ...,
        kind: str = ...,
        state: JobState = ...,
        created_at: datetime | None = ...,
    ) -> str: ...
