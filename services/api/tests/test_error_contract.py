"""The error contract is public API. These tests pin it down.

They target the sign-in route because it is the one endpoint that validates a
body before touching any dependency, which lets the whole contract be exercised
against an app with unreachable infrastructure.
"""

from __future__ import annotations

import httpx

from agentrail_core.errors import ErrorCode

SIGN_IN = "/api/v1/auth/dev/session"


class TestValidationErrors:
    async def test_missing_field_returns_the_problem_contract(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.post(SIGN_IN, json={})

        assert response.status_code == 422
        body = response.json()
        assert body["code"] == ErrorCode.VALIDATION_FAILED
        assert body["correlation_id"].startswith("cid_")
        assert body["details"]["errors"][0]["location"] == ["body", "email"]

    async def test_unknown_field_is_rejected(self, offline_client: httpx.AsyncClient) -> None:
        response = await offline_client.post(
            SIGN_IN, json={"email": "ada@example.com", "sneaky": 1}
        )

        assert response.status_code == 422
        assert response.json()["code"] == ErrorCode.VALIDATION_FAILED

    async def test_a_malformed_email_is_rejected(self, offline_client: httpx.AsyncClient) -> None:
        response = await offline_client.post(SIGN_IN, json={"email": "not-an-email"})

        assert response.status_code == 422

    async def test_authentication_is_checked_before_path_validation(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        """An anonymous caller cannot even use validation errors as a probe.

        A malformed identifier on a protected route reports 401, not 422, so the
        shape of valid identifiers is not disclosed to unauthenticated callers.
        """
        response = await offline_client.get("/api/v1/jobs/not-a-ulid")

        assert response.status_code == 401


class TestRequestLimits:
    async def test_body_over_the_limit_is_rejected_with_413(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.post(SIGN_IN, json={"email": "x" * 200_000})

        assert response.status_code == 413
        body = response.json()
        assert body["code"] == ErrorCode.PAYLOAD_TOO_LARGE
        assert body["details"]["max_bytes"] == 64 * 1024


class TestErrorResponseShape:
    async def test_every_error_carries_a_correlation_id_header_and_body_field(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.post(SIGN_IN, json={})

        assert response.headers["x-correlation-id"] == response.json()["correlation_id"]

    async def test_client_supplied_correlation_id_is_used_in_the_error(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.post(
            SIGN_IN, json={}, headers={"x-correlation-id": "cid_client"}
        )

        assert response.json()["correlation_id"] == "cid_client"

    async def test_error_body_never_contains_a_traceback(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.post(SIGN_IN, json={})

        assert set(response.json()) == {"code", "message", "correlation_id", "details"}

    async def test_unauthenticated_requests_use_the_same_contract(
        self, offline_client: httpx.AsyncClient
    ) -> None:
        response = await offline_client.get("/api/v1/organisations")

        assert response.status_code == 401
        assert set(response.json()) == {"code", "message", "correlation_id", "details"}
        assert response.json()["code"] == ErrorCode.UNAUTHENTICATED
