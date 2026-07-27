"""Shared pytest fixtures.

Integration tests run against **real** PostgreSQL and Redis — never a stub and
never SQLite. The schema is created by running the actual Alembic migrations, so
every integration run also verifies that the migrations apply cleanly.

Behaviour when the dependencies are absent:

* by default the integration tests are skipped, so ``make test`` works on a
  laptop with nothing running;
* when ``AGENTRAIL_REQUIRE_INTEGRATION=1`` (which CI sets) a missing dependency
  is a hard failure, so the suite can never silently pass by skipping.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import redis.asyncio as redis
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agentrail_core.db import create_database_engine, create_session_factory
from agentrail_core.queue import create_redis_client
from agentrail_core.settings import DatabaseSettings, QueueSettings

REPO_ROOT = Path(__file__).parent
ALEMBIC_INI = REPO_ROOT / "services" / "api" / "alembic.ini"


def _integration_required() -> bool:
    return os.environ.get("AGENTRAIL_REQUIRE_INTEGRATION", "0") == "1"


def _unavailable(dependency: str, reason: str) -> None:
    detail = f"{dependency} is unavailable for integration tests: {reason}"
    if _integration_required():
        pytest.fail(f"{detail} (AGENTRAIL_REQUIRE_INTEGRATION=1)")
    pytest.skip(detail)


@pytest.fixture(scope="session")
def database_settings() -> DatabaseSettings:
    return DatabaseSettings(service_name="agentrail-tests")


@pytest.fixture(scope="session")
def queue_settings() -> QueueSettings:
    """Queue settings pointing at a queue key unique to this test session.

    Isolating the key means the suite never consumes or deletes messages
    belonging to a developer's running stack.
    """
    suffix = uuid.uuid4().hex
    return QueueSettings(
        job_queue_key=f"agentrail:test:{suffix}:jobs",
        run_queue_key=f"agentrail:test:{suffix}:runs",
    )


@pytest.fixture(scope="session")
def migrated_database(database_settings: DatabaseSettings) -> str:
    """Apply the real Alembic migrations once per session."""
    url = database_settings.sync_database_url
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    config.set_main_option("sqlalchemy.url", url)

    try:
        command.upgrade(config, "head")
    except Exception as exc:  # noqa: BLE001 - any failure means "not available"
        _unavailable("PostgreSQL", f"{type(exc).__name__}: {exc}")
    return url


@pytest.fixture
async def db_engine(
    database_settings: DatabaseSettings, migrated_database: str
) -> AsyncIterator[AsyncEngine]:
    engine = create_database_engine(database_settings)
    try:
        async with engine.begin() as connection:
            # One statement so foreign keys never block the reset, and CASCADE so
            # a table added in a later phase does not silently survive.
            await connection.execute(
                text(
                    "TRUNCATE TABLE jobs, outbox_events, comparison_reports, "
                    "evaluation_results, evaluator_versions, trajectory_replays, "
                    "trajectory_checkpoints, trajectory_steps, trajectories, "
                    "run_items, evaluation_runs, "
                    "evaluation_suites, dataset_versions, datasets, "
                    "audit_events, api_keys, sessions, "
                    "memberships, projects, organisations, users CASCADE"
                )
            )
    except SQLAlchemyError as exc:
        await engine.dispose()
        _unavailable("PostgreSQL", f"{type(exc).__name__}")
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest.fixture
async def redis_client(queue_settings: QueueSettings) -> AsyncIterator[redis.Redis]:
    client = create_redis_client(queue_settings)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure means "not available"
        await client.aclose()
        _unavailable("Redis", f"{type(exc).__name__}")
    # Only the session's own key is removed; nothing else in the database is touched.
    await client.delete(queue_settings.job_queue_key, queue_settings.run_queue_key)
    try:
        yield client
    finally:
        await client.delete(queue_settings.job_queue_key, queue_settings.run_queue_key)
        await client.aclose()


@pytest.fixture(scope="session", autouse=True)
def _test_environment() -> Iterator[None]:
    """Mark the process as a test environment for anything that inspects it."""
    previous = os.environ.get("AGENTRAIL_ENVIRONMENT")
    os.environ["AGENTRAIL_ENVIRONMENT"] = "test"
    yield
    if previous is None:
        del os.environ["AGENTRAIL_ENVIRONMENT"]
    else:
        os.environ["AGENTRAIL_ENVIRONMENT"] = previous
