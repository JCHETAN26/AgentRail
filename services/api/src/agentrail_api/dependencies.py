"""FastAPI dependency wiring.

Infrastructure clients are created once in the lifespan handler and stored on
``app.state``; these helpers hand them to routes. Nothing constructs an engine
or a Redis client per request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[redis.Redis, Depends(get_redis)]
ContextDep = Annotated[CorrelationContext, Depends(get_correlation_context)]
