"""FastAPI dependency wiring.

Infrastructure clients are created once in the lifespan handler and stored on
``app.state``; these helpers hand them to routes. Nothing constructs an engine
or a Redis client per request.

Authentication is a dependency too: a route that wants a caller declares
``ActorDep`` and cannot accidentally serve an anonymous request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_api.auth.providers import AuthProvider, DevAuthProvider, GitHubOAuthProvider
from agentrail_api.auth.service import SESSION_COOKIE_NAME, Actor, authenticate
from agentrail_api.security import enforce_authenticated_rate_limit
from agentrail_api.settings import ApiSettings
from agentrail_core.correlation import CorrelationContext, context_from_headers
from agentrail_core.github import CheckRunPublisher, RecordingCheckRunPublisher


def get_settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_redis(request: Request) -> redis.Redis:
    client: redis.Redis = request.app.state.redis
    return client


def get_check_run_publisher(request: Request) -> CheckRunPublisher:
    """Where gate verdicts are delivered.

    The recording publisher is the default and the only one wired today, so the
    platform is fully exercisable with no GitHub App — the same reasoning as the
    deterministic auth provider. A real publisher slots in behind this protocol
    without any caller changing.
    """
    publisher = getattr(request.app.state, "check_run_publisher", None)
    if publisher is None:
        publisher = RecordingCheckRunPublisher()
        request.app.state.check_run_publisher = publisher
    return publisher


def get_correlation_context(request: Request) -> CorrelationContext:
    """The context bound by ``CorrelationMiddleware`` for this request."""
    context = request.scope.get("correlation_context")
    if isinstance(context, CorrelationContext):
        return context
    # Only reachable if a route is exercised without the middleware installed.
    return context_from_headers(dict(request.headers))


async def get_session(
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    """Provide a session. Routes commit explicitly; anything unfinished rolls back."""
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


SettingsDep = Annotated[ApiSettings, Depends(get_settings)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CheckRunPublisherDep = Annotated[CheckRunPublisher, Depends(get_check_run_publisher)]
RedisDep = Annotated[redis.Redis, Depends(get_redis)]
ContextDep = Annotated[CorrelationContext, Depends(get_correlation_context)]


def build_auth_provider(settings: ApiSettings, name: str) -> AuthProvider:
    """Resolve a provider by name, refusing anything unavailable here.

    The dev provider is passwordless, so the guard against returning it in a
    deployed environment is a security control, not a convenience.
    """
    if name == "github":
        if not settings.github_oauth_configured:
            raise ValueError("GitHub OAuth is not configured.")
        assert settings.github_oauth_client_id is not None
        assert settings.github_oauth_secret is not None
        return GitHubOAuthProvider(
            client_id=settings.github_oauth_client_id,
            client_secret=settings.github_oauth_secret,
        )
    if name == "dev":
        if not settings.dev_auth_enabled:
            raise ValueError("The dev provider is disabled in deployed environments.")
        return DevAuthProvider()
    raise ValueError(f"Unknown auth provider: {name}")


async def get_actor(
    session: SessionDep,
    request: Request,
    client: RedisDep,
    settings: SettingsDep,
    context: ContextDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Actor:
    """Authenticate the caller, or raise 401."""
    actor = await authenticate(
        session,
        cookie_token=request.cookies.get(SESSION_COOKIE_NAME),
        authorization=authorization,
        client_host=request.client.host if request.client is not None else None,
        user_agent=request.headers.get("user-agent"),
        inactivity_anomaly_days=settings.api_key_inactivity_anomaly_days,
        fingerprint_secret=settings.api_key_fingerprint_secret_value,
        correlation_id=context.correlation_id,
    )
    if actor.user is not None:
        await enforce_authenticated_rate_limit(
            client, settings, actor_kind="user", actor_id=actor.user.id
        )
    elif actor.api_key is not None:
        await enforce_authenticated_rate_limit(
            client, settings, actor_kind="api_key", actor_id=actor.api_key.id
        )
    return actor


ActorDep = Annotated[Actor, Depends(get_actor)]
