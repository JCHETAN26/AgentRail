from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from agentrail_core.settings import DatabaseSettings, QueueSettings


class ApiSettings(DatabaseSettings, QueueSettings):
    """Configuration for the platform API process."""

    service_name: str = "agentrail-api"

    #: Origins permitted to call the API from a browser. The local web app only.
    cors_allow_origins: tuple[str, ...] = ("http://localhost:3000",)

    #: Hard cap on request bodies. Phase 0 accepts only tiny JSON documents;
    #: dataset upload in Phase 4 will introduce a separate streaming path.
    max_request_bytes: int = Field(default=64 * 1024, ge=1024, le=10 * 1024 * 1024)


@lru_cache(maxsize=1)
def api_settings() -> ApiSettings:
    return ApiSettings()
