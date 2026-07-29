from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator

from agentrail_core.settings import DatabaseSettings, Environment, QueueSettings


class ApiSettings(DatabaseSettings, QueueSettings):
    """Configuration for the platform API process."""

    service_name: str = "agentrail-api"

    #: Origins permitted to call the API from a browser. The local web app only.
    cors_allow_origins: tuple[str, ...] = ("http://localhost:3737",)

    #: Hard cap on request bodies. Phase 0 accepts only tiny JSON documents;
    #: dataset upload in Phase 4 will introduce a separate streaming path.
    max_request_bytes: int = Field(default=64 * 1024, ge=1024, le=10 * 1024 * 1024)

    #: Fixed-window limit for authenticated API callers. The key is the user or
    #: API-key id, so one tenant cannot spend another tenant's request budget.
    api_rate_limit_requests: int = Field(default=600, ge=1, le=100_000)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)

    #: Durable monthly cap on evaluation workload per organisation. The charge
    #: unit is one run item, so a suite with 50 dataset records spends 50.
    evaluation_item_monthly_quota: int = Field(default=50_000, ge=1, le=10_000_000)

    #: Audit events older than this may be pruned by an organisation admin.
    audit_event_retention_days: int = Field(default=365, ge=1, le=3650)

    #: A successful API-key request after this many idle days is audited as an
    #: anomaly. The key is not blocked; operators get durable evidence instead.
    api_key_inactivity_anomaly_days: int = Field(default=30, ge=1, le=3650)
    #: Secret used to HMAC API-key client fingerprints before persistence.
    #: Local/CI environments use a deterministic development key; deployed
    #: environments must provide a real secret so DB readers cannot enumerate
    #: likely IP addresses or user-agent strings.
    api_key_fingerprint_secret: str | None = None

    # --- Authentication ----------------------------------------------------
    #: Where the console lives. Sign-in redirects back here.
    web_base_url: str = "http://localhost:3737"
    session_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=300, le=60 * 60 * 24 * 90)

    #: GitHub OAuth application credentials. Required in deployed environments,
    #: absent everywhere else — the deterministic dev provider needs neither.
    github_oauth_client_id: str | None = None
    github_oauth_secret: str | None = None

    # --- Release gates and GitHub integration ------------------------------
    #: Shared secret GitHub signs webhook bodies with. Absent locally, which is
    #: why the webhook endpoint rejects everything until it is configured — an
    #: unauthenticated public write is worse than a disabled integration.
    github_webhook_secret: str | None = None
    github_webhook_replay_ttl_seconds: int = Field(default=60 * 10, ge=60, le=60 * 60 * 24)

    # --- Model providers ---------------------------------------------------
    #: Optional OpenAI-compatible Responses API credentials for live Tribunal
    #: debate. Absent by default so local development and CI stay deterministic.
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    tribunal_model_timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    @property
    def cookies_are_secure(self) -> bool:
        """`Secure` is set on deployed environments only, so local HTTP works."""
        return self.environment.is_deployed

    @property
    def dev_auth_enabled(self) -> bool:
        """The passwordless dev provider is never available once deployed."""
        return not self.environment.is_deployed

    @property
    def github_oauth_configured(self) -> bool:
        return bool(self.github_oauth_client_id and self.github_oauth_secret)

    @property
    def api_key_fingerprint_secret_value(self) -> str:
        if self.api_key_fingerprint_secret:
            return self.api_key_fingerprint_secret
        return f"agentrail-local-api-key-fingerprint:{self.environment.value}"

    @model_validator(mode="after")
    def _deployed_environments_need_real_auth(self) -> ApiSettings:
        """Fail fast rather than silently exposing passwordless sign-in.

        A deployed environment without OAuth credentials would otherwise start
        with no usable sign-in at all — or, worse, a future edit could leave the
        dev provider reachable in production.
        """
        if self.environment.is_deployed and not self.github_oauth_configured:
            raise ValueError(
                "AGENTRAIL_GITHUB_OAUTH_CLIENT_ID and AGENTRAIL_GITHUB_OAUTH_SECRET are "
                f"required when AGENTRAIL_ENVIRONMENT is {self.environment.value}."
            )
        if self.environment.is_deployed and not self.api_key_fingerprint_secret:
            raise ValueError(
                "AGENTRAIL_API_KEY_FINGERPRINT_SECRET is required when "
                f"AGENTRAIL_ENVIRONMENT is {self.environment.value}."
            )
        return self


@lru_cache(maxsize=1)
def api_settings() -> ApiSettings:
    return ApiSettings()


__all__ = ["ApiSettings", "Environment", "api_settings"]
