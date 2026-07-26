"""Deterministic synthetic CloudOps environment.

Everything this service returns is synthetic. It models no real infrastructure
and must never be described as production telemetry.

Phase 0 ships only the deterministic no-op task that proves the worker can reach
a tool surface. The synthetic services, metrics, logs, runbooks and incident
families described in the build plan arrive in Phase 2.
"""

__version__ = "0.1.0"
