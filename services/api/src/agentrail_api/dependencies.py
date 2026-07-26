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
from agentrail_api.settings import ApiSettings
from agentrail_core.correlation import CorrelationContext, context_from_headers


def get_settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_redis(request: Request) -> redis.Redis:
    client: redis.Redis = request.app.state.redis
    return client


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
    authorization: Annotated[str | None, Header()] = None,
) -> Actor:
    """Authenticate the caller, or raise 401."""
    return await authenticate(
        session,
        cookie_token=request.cookies.get(SESSION_COOKIE_NAME),
        authorization=authorization,
    )


ActorDep = Annotated[Actor, Depends(get_actor)]
