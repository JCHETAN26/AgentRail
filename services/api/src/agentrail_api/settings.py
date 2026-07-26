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

    # --- Authentication ----------------------------------------------------
    #: Where the console lives. Sign-in redirects back here.
    web_base_url: str = "http://localhost:3737"
    session_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=300, le=60 * 60 * 24 * 90)

    #: GitHub OAuth application credentials. Required in deployed environments,
    #: absent everywhere else — the deterministic dev provider needs neither.
    github_oauth_client_id: str | None = None
    github_oauth_secret: str | None = None

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
        return self


@lru_cache(maxsize=1)
def api_settings() -> ApiSettings:
    return ApiSettings()


__all__ = ["ApiSettings", "Environment", "api_settings"]
