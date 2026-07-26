"""Central environment parsing.

Configuration is read once, at process start, through pydantic-settings. A
service that reads ``os.environ`` directly is a bug: it bypasses validation and
makes the effective configuration impossible to log or test.

All variables use the ``AGENTRAIL_`` prefix. Defaults are safe for local
development only — they contain no credentials that could be valid anywhere
else, and production deployments must supply every value explicitly.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    CI = "ci"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_deployed(self) -> bool:
        return self in (Environment.STAGING, Environment.PRODUCTION)


class CoreSettings(BaseSettings):
    """Settings shared by every AgentRail service."""

    model_config = SettingsConfigDict(
        env_prefix="AGENTRAIL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = Environment.LOCAL
    service_name: str = "agentrail"
    log_level: str = "INFO"

    #: Seconds a service may spend draining in-flight work on SIGTERM.
    shutdown_grace_seconds: float = Field(default=15.0, gt=0, le=120)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return upper


class DatabaseSettings(CoreSettings):
    """Settings for services that own PostgreSQL connections."""

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+psycopg://agentrail:agentrail@localhost:5432/agentrail"),
        description="SQLAlchemy URL. Must use the psycopg driver.",
    )
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_pool_max_overflow: int = Field(default=5, ge=0, le=50)
    database_statement_timeout_ms: int = Field(default=10_000, ge=100, le=300_000)

    @property
    def sync_database_url(self) -> str:
        """The same URL for tooling that requires a synchronous driver (Alembic)."""
        return str(self.database_url)


class QueueSettings(CoreSettings):
    """Settings for services that publish to or consume from Redis.

    Redis carries *delivery* only. PostgreSQL remains the authoritative store
    for job state; a lost Redis message must never lose a job.
    """

    redis_url: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))
    job_queue_key: str = Field(default="agentrail:jobs:pending", min_length=1)
    #: Integer seconds because Redis ``BLPOP`` takes an integer timeout.
    queue_block_timeout_seconds: int = Field(default=2, ge=1, le=60)


@lru_cache(maxsize=1)
def core_settings() -> CoreSettings:
    return CoreSettings()
