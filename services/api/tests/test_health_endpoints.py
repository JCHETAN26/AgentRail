from __future__ import annotations

import httpx
import pytest


class TestLiveness:
    async def test_healthz_does_not_touch_dependencies(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        """Liveness must stay green when PostgreSQL and Redis are unreachable.

        The ``offline_client`` app has clients that were never connected, which
        is exactly the situation this asserts.
        """
        response = await offline_client.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "service": "agentrail-api-tests",
            "version": "0.1.0",
        }


class TestReadiness:
    @pytest.mark.integration
    async def test_readyz_reports_every_dependency_up(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/readyz")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert {item["name"]: item["status"] for item in body["dependencies"]} == {
            "postgresql": "up",
            "redis": "up",
        }

    async def test_readyz_returns_503_when_a_dependency_is_down(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        # The offline app points at ports with nothing listening.
        response = await offline_client.get("/readyz")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert any(item["status"] == "down" for item in body["dependencies"])
