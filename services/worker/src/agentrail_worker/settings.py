from __future__ import annotations

import os
import socket
from functools import lru_cache

from pydantic import AnyHttpUrl, Field

from agentrail_core.settings import DatabaseSettings, QueueSettings


def _default_worker_id() -> str:
    """Identify the worker instance in logs and in ``jobs.worker_id``."""
    return f"{socket.gethostname()}-{os.getpid()}"


class WorkerSettings(DatabaseSettings, QueueSettings):
    service_name: str = "agentrail-worker"

    worker_id: str = Field(default_factory=_default_worker_id, max_length=128)
    sandbox_base_url: AnyHttpUrl = Field(default=AnyHttpUrl("http://localhost:8100"))
    sandbox_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    health_port: int = Field(default=8200, ge=1, le=65535)

    #: A job left ``PENDING`` for longer than this is re-published. This covers
    #: the window where the API committed the row but the Redis publish failed.
    #: Phase 5 replaces the sweep with a transactional outbox.
    stale_pending_seconds: float = Field(default=30.0, gt=0, le=3600)
    recovery_sweep_interval_seconds: float = Field(default=15.0, gt=0, le=600)
    recovery_sweep_batch_size: int = Field(default=100, ge=1, le=1000)
    run_item_lease_seconds: float = Field(default=30.0, gt=0, le=600)


@lru_cache(maxsize=1)
def worker_settings() -> WorkerSettings:
    return WorkerSettings()
