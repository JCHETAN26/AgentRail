"""Deterministic sandbox tasks.

Pure functions: no clock, no randomness, no I/O. Determinism is the whole point —
CI must be able to assert on exact outputs, and a replayed run must produce
byte-identical results.
"""

from __future__ import annotations

import hashlib
from typing import TypedDict

from agentrail_cloudops_sandbox import __version__

DIGEST_LENGTH = 16


class NoopResult(TypedDict):
    """Result of the deterministic no-op task."""

    echo: str
    digest: str
    sandbox_version: str


def execute_noop(message: str) -> NoopResult:
    """Echo ``message`` alongside a stable digest of it.

    The digest exists so a caller can prove the payload survived the whole
    web → API → queue → worker → sandbox path unmodified.
    """
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]
    return NoopResult(echo=message, digest=digest, sandbox_version=__version__)
