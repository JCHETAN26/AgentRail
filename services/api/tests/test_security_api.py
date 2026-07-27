"""Phase 14 runtime security controls."""

from __future__ import annotations

import httpx
import pytest
import redis.asyncio as redis
from api_test_support import Tenant
from fastapi import FastAPI

pytestmark = pytest.mark.integration


async def _clear_rate_keys(client: redis.Redis) -> None:
    keys = [key async for key in client.scan_iter("agentrail:rate:*")]
    if keys:
        await client.delete(*keys)


class TestAuthenticatedRateLimit:
    async def test_authenticated_callers_are_capped_by_identity(
        self, integration_app: FastAPI, redis_client: redis.Redis, tenant: Tenant
    ) -> None:
        original_settings = integration_app.state.settings
        integration_app.state.settings = original_settings.model_copy(
            update={"api_rate_limit_requests": 1, "api_rate_limit_window_seconds": 60}
        )
        await _clear_rate_keys(redis_client)
        try:
            first = await tenant.client.get("/api/v1/auth/me")
            second = await tenant.client.get("/api/v1/auth/me")
        finally:
            integration_app.state.settings = original_settings
            await _clear_rate_keys(redis_client)

        assert first.status_code == 200
        assert second.status_code == 429
        assert second.json()["code"] == "rate_limited"
        assert second.json()["details"] == {"limit": 1, "window_seconds": 60}

    async def test_anonymous_auth_failures_do_not_spend_a_caller_bucket(
        self,
        integration_app: FastAPI,
        redis_client: redis.Redis,
        anonymous_client: httpx.AsyncClient,
    ) -> None:
        original_settings = integration_app.state.settings
        integration_app.state.settings = original_settings.model_copy(
            update={"api_rate_limit_requests": 1, "api_rate_limit_window_seconds": 60}
        )
        await _clear_rate_keys(redis_client)
        try:
            first = await anonymous_client.get("/api/v1/auth/me")
            second = await anonymous_client.get("/api/v1/auth/me")
        finally:
            integration_app.state.settings = original_settings
            await _clear_rate_keys(redis_client)

        assert first.status_code == 401
        assert second.status_code == 401
