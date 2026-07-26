"""The error contract is public API. These tests pin it down."""

from __future__ import annotations

import httpx

from agentrail_core.errors import ErrorCode


class TestValidationErrors:
    async def test_missing_field_returns_the_problem_contract(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.post("/api/v1/jobs", json={})

        assert response.status_code == 422
        body = response.json()
        assert body["code"] == ErrorCode.VALIDATION_FAILED
        assert body["correlation_id"].startswith("cid_")
        assert body["details"]["errors"][0]["location"] == ["body", "message"]

    async def test_unknown_field_is_rejected(self, offline_client: httpx.AsyncClient) -> None:
        response = await offline_client.post(
            "/api/v1/jobs", json={"message": "hi", "kind": "noop", "sneaky": 1}
        )

        assert response.status_code == 422
        assert response.json()["code"] == ErrorCode.VALIDATION_FAILED

    async def test_unknown_job_kind_is_rejected(self, offline_client: httpx.AsyncClient) -> None:
        response = await offline_client.post(
            "/api/v1/jobs", json={"message": "hi", "kind": "delete_production"}
        )

        assert response.status_code == 422

    async def test_oversized_message_is_rejected(self, offline_client: httpx.AsyncClient) -> None:
        response = await offline_client.post("/api/v1/jobs", json={"message": "x" * 501})

        assert response.status_code == 422

    async def test_malformed_job_id_is_rejected_before_a_database_lookup(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.get("/api/v1/jobs/not-a-ulid")

        assert response.status_code == 422

    async def test_page_size_above_the_maximum_is_rejected(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.get("/api/v1/jobs", params={"limit": 1000})

        assert response.status_code == 422


class TestRequestLimits:
    async def test_body_over_the_limit_is_rejected_with_413(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        oversized = {"message": "x" * 200_000}

        response = await offline_client.post("/api/v1/jobs", json=oversized)

        assert response.status_code == 413
        body = response.json()
        assert body["code"] == ErrorCode.PAYLOAD_TOO_LARGE
        assert body["details"]["max_bytes"] == 64 * 1024


class TestErrorResponseShape:
    async def test_every_error_carries_a_correlation_id_header_and_body_field(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.post("/api/v1/jobs", json={})

        assert response.headers["x-correlation-id"] == response.json()["correlation_id"]

    async def test_client_supplied_correlation_id_is_used_in_the_error(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.post(
            "/api/v1/jobs", json={}, headers={"x-correlation-id": "cid_client"}
        )

        assert response.json()["correlation_id"] == "cid_client"

    async def test_error_body_never_contains_a_traceback(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.post("/api/v1/jobs", json={})

        assert set(response.json()) == {"code", "message", "correlation_id", "details"}
