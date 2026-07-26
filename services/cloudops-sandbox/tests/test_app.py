from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from agentrail_cloudops_sandbox.app import SandboxSettings, create_app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(SandboxSettings(_env_file=None, environment="test"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sandbox") as http_client:
        yield http_client


class TestHealthEndpoints:
    async def test_healthz_reports_ok_without_touching_dependencies(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/healthz")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_readyz_reports_ready_with_no_dependencies(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/readyz")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["dependencies"] == []


class TestNoopTask:
    async def test_returns_the_deterministic_result(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/tasks/noop", json={"message": "hello"})

        assert response.status_code == 200
        assert response.json() == {
            "echo": "hello",
            "digest": "2cf24dba5fb0a30e",
            "sandbox_version": "0.1.0",
        }

    async def test_rejects_an_empty_message(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/tasks/noop", json={"message": ""})

        assert response.status_code == 422

    async def test_rejects_an_oversized_message(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/tasks/noop", json={"message": "x" * 501})

        assert response.status_code == 422

    async def test_rejects_unknown_fields(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/v1/tasks/noop", json={"message": "hello", "unexpected": True}
        )

        assert response.status_code == 422


class TestCorrelationPropagation:
    async def test_response_carries_a_correlation_id(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/v1/tasks/noop", json={"message": "hello"})

        assert response.headers["x-correlation-id"].startswith("cid_")

    async def test_inbound_correlation_id_is_echoed_back(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/v1/tasks/noop",
            json={"message": "hello"},
            headers={"x-correlation-id": "cid_from_worker"},
        )

        assert response.headers["x-correlation-id"] == "cid_from_worker"

    async def test_inbound_trace_is_continued(self, client: httpx.AsyncClient) -> None:
        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

        response = await client.post(
            "/v1/tasks/noop", json={"message": "hello"}, headers={"traceparent": traceparent}
        )

        assert "4bf92f3577b34da6a3ce929d0e0e4736" in response.headers["traceparent"]
