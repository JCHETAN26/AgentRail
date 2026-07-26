from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import redis.asyncio as redis
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from agentrail_api.app import attach_infrastructure, create_app
from agentrail_api.settings import ApiSettings
from agentrail_core.settings import QueueSettings

#: Port 1 is reserved and never has a listener, so connections fail immediately
#: rather than hanging or — worse — reaching a developer's running stack.
UNREACHABLE = 1


@pytest.fixture
def api_settings(queue_settings: QueueSettings) -> ApiSettings:
    return ApiSettings(
        service_name="agentrail-api-tests", job_queue_key=queue_settings.job_queue_key
    )


@pytest.fixture
def offline_settings() -> ApiSettings:
    return ApiSettings(
        service_name="agentrail-api-tests",
        database_url=f"postgresql+psycopg://nobody:nobody@127.0.0.1:{UNREACHABLE}/nowhere",
        redis_url=f"redis://127.0.0.1:{UNREACHABLE}/0",
    )


@pytest.fixture
def offline_app(offline_settings: ApiSettings) -> FastAPI:
    """An app whose dependencies are guaranteed unreachable.

    Enough to exercise middleware, validation and the error contract without any
    infrastructure, and to prove that liveness stays green while readiness does
    not. Routes that touch PostgreSQL belong in the integration tests.
    """
    app = create_app(offline_settings)
    attach_infrastructure(app)
    return app


@pytest.fixture
async def offline_client(offline_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=offline_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        yield client


@pytest.fixture
def integration_app(
    api_settings: ApiSettings, db_engine: AsyncEngine, redis_client: redis.Redis
) -> FastAPI:
    """An app wired to the real migrated database and a real Redis client."""
    app = create_app(api_settings)
    attach_infrastructure(app, engine=db_engine, redis_client=redis_client)
    return app


@pytest.fixture
async def client(integration_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=integration_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as http_client:
        yield http_client
