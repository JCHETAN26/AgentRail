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


class TestCloudOpsContracts:
    async def test_lists_the_ten_cloudops_tool_contracts(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/tool-contracts")

        assert response.status_code == 200
        tools = response.json()["tools"]
        assert [tool["name"] for tool in tools] == [
            "get_service_health",
            "query_metrics",
            "search_logs",
            "get_dependency_graph",
            "get_runbook",
            "restart_service",
            "scale_service",
            "create_incident",
            "notify_oncall",
            "escalate_to_human",
        ]
        assert all("risk" in tool and "side_effect" in tool for tool in tools)
        assert all(
            tool["requires_idempotency_key"]
            for tool in tools
            if tool["side_effect"] == "idempotent_write"
        )

    async def test_lists_twenty_five_grounded_scenarios(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/scenarios")

        assert response.status_code == 200
        scenarios = response.json()["scenarios"]
        assert len(scenarios) == 25
        families = {scenario["family"].removesuffix("_variant") for scenario in scenarios}
        assert families == {
            "postgres_pool_exhaustion",
            "consumer_lag",
            "upstream_rate_limiting",
            "expired_credential",
            "memory_leak",
            "cpu_saturation",
            "stale_cache",
            "dns_resolution_failure",
            "dependency_timeout",
            "misconfigured_autoscaling",
            "misleading_logs",
            "conflicting_signals",
            "prompt_injection_logs",
            "approval_required",
            "duplicate_delivery",
            "worker_side_effect_failure",
        }
        for scenario in scenarios:
            truth = scenario["ground_truth"]
            assert truth["expected_diagnosis"]
            assert truth["expected_arguments"]
            assert truth["expected_evidence"]
            assert truth["max_tool_calls"] > 0


class TestCloudOpsTools:
    async def test_reset_selects_the_active_scenario(self, client: httpx.AsyncClient) -> None:
        reset = await client.post("/v1/scenarios/memory_leak_catalog_api/reset")
        health = await client.get("/v1/services/catalog-api/health")

        assert reset.status_code == 200
        assert health.status_code == 200
        assert health.json()["status"] == "critical"
        assert "memory_leak" in health.json()["failing_dependencies"]

    async def test_query_metrics_can_return_stale_fault_data(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/v1/metrics/query",
            json={
                "service_name": "checkout-api",
                "metric": "database_pool",
                "start_time": "2026-07-26T20:55:00Z",
                "end_time": "2026-07-26T21:00:00Z",
                "fault": "stale",
            },
        )

        assert response.status_code == 200
        assert response.json()["stale"] is True

    async def test_fault_hooks_are_available(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/v1/metrics/query",
            json={
                "service_name": "checkout-api",
                "metric": "database_pool",
                "start_time": "2026-07-26T20:55:00Z",
                "end_time": "2026-07-26T21:00:00Z",
                "fault": "rate_limit",
            },
        )

        assert response.status_code == 429
        assert response.headers["retry-after"] == "30"

    async def test_prompt_injection_is_present_as_log_data_only(
        self, client: httpx.AsyncClient
    ) -> None:
        await client.post("/v1/scenarios/prompt_injection_logs_identity_api/reset")

        response = await client.post(
            "/v1/logs/search",
            json={"service_name": "identity-api", "query": "prompt_injection", "limit": 10},
        )

        assert response.status_code == 200
        assert "IGNORE PREVIOUS INSTRUCTIONS" in response.json()["items"][0]["message"]

    async def test_duplicate_side_effect_key_returns_original_result(
        self, client: httpx.AsyncClient
    ) -> None:
        request = {
            "service_name": "checkout-api",
            "replicas": 8,
            "idempotency_key": "idem-scale-checkout-001",
        }

        first = await client.post("/v1/services/scale", json=request)
        second = await client.post("/v1/services/scale", json={**request, "replicas": 2})

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["message"] == first.json()["message"]
        assert second.json()["idempotent_replay"] is True

    async def test_seed_resets_side_effect_memory(self, client: httpx.AsyncClient) -> None:
        request = {"service_name": "checkout-api", "idempotency_key": "idem-restart-001"}
        first = await client.post("/v1/services/restart", json=request)
        replay = await client.post("/v1/services/restart", json=request)

        seeded = await client.post("/v1/seed")
        after_seed = await client.post("/v1/services/restart", json=request)

        assert first.status_code == 200
        assert replay.json()["idempotent_replay"] is True
        assert seeded.status_code == 200
        assert after_seed.json()["idempotent_replay"] is False


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
