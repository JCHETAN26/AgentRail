"""PostgreSQL engine and session construction.

PostgreSQL is the authoritative store for all run state. Engines are created
once per process and disposed on shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from agentrail_core.errors import DependencyUnavailableError
from agentrail_core.settings import DatabaseSettings


class Base(DeclarativeBase):
    """Declarative base shared by every AgentRail table."""


def create_database_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Build the process-wide async engine.

    A server-side ``statement_timeout`` is set on every connection so that a
    pathological query cannot pin a worker or an API request indefinitely.

    The session timezone is pinned to UTC. ``timestamptz`` values are rendered
    in the session's zone, so without this the same instant serialises as
    ``...Z`` when it was just written in-process and ``...-07:00`` when it was
    read back from PostgreSQL — two representations of one value from one
    endpoint, depending only on where the row came from. CI runs in UTC and
    cannot see the difference; a developer outside UTC can.
    """
    return create_async_engine(
        str(settings.database_url),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
        pool_pre_ping=True,
        future=True,
        connect_args={
            "options": (
                f"-c statement_timeout={settings.database_statement_timeout_ms} -c timezone=UTC"
            ),
            "application_name": settings.service_name,
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Run a unit of work in a transaction, committing on success."""
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def set_tenant_context(session: AsyncSession, organisation_id: str) -> None:
    """Bind the current transaction to one tenant for Postgres RLS policies."""
    await session.execute(
        text("SELECT set_config('agentrail.organisation_id', :organisation_id, true)"),
        {"organisation_id": organisation_id},
    )


async def check_database(engine: AsyncEngine) -> None:
    """Raise :class:`DependencyUnavailableError` if PostgreSQL is not usable."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:  # pragma: no cover - exercised in integration tests
        raise DependencyUnavailableError(
            "PostgreSQL is not reachable", details={"dependency": "postgresql"}
        ) from exc
