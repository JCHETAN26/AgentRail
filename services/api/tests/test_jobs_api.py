"""Integration tests for the jobs resource against real PostgreSQL and Redis."""

from __future__ import annotations

import pytest
import redis.asyncio as redis
from api_test_support import Tenant
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.ids import is_sortable_id
from agentrail_core.jobs import Job, JobState
from agentrail_core.settings import QueueSettings

pytestmark = pytest.mark.integration


async def queued_ids(client: redis.Redis, key: str) -> list[str]:
    return [str(value) for value in await client.lrange(key, 0, -1)]


class TestCreateJob:
    async def test_creates_a_pending_job_and_publishes_its_id(
        self,
        tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client: redis.Redis,
        queue_settings: QueueSettings,
    ) -> None:
        response = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/jobs", json={"message": "hello"}
        )

        assert response.status_code == 201
        body = response.json()
        assert is_sortable_id(body["id"])
        assert body["project_id"] == tenant.project_id
        assert body["state"] == JobState.PENDING
        assert body["payload"] == {"message": "hello"}
        assert body["result"] is None
        assert body["attempts"] == 0

        async with session_factory() as session:
            stored = await session.get(Job, body["id"])
        assert stored is not None
        assert stored.state == JobState.PENDING
        assert stored.project_id == tenant.project_id

        assert await queued_ids(redis_client, queue_settings.job_queue_key) == [body["id"]]

    async def test_records_the_request_correlation_id_on_the_job(self, tenant: Tenant) -> None:
        response = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/jobs",
            json={"message": "hello"},
            headers={"x-correlation-id": "cid_web"},
        )

        assert response.json()["correlation_id"] == "cid_web"

    async def test_continues_the_inbound_trace(self, tenant: Tenant) -> None:
        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

        response = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/jobs",
            json={"message": "hello"},
            headers={"traceparent": traceparent},
        )

        assert response.json()["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"

    async def test_a_job_in_an_unknown_project_is_refused(self, tenant: Tenant) -> None:
        response = await tenant.client.post(
            "/api/v1/projects/01ARZ3NDEKTSV4RRFFQ69G5FAV/jobs", json={"message": "hello"}
        )

        assert response.status_code == 403


class TestIdempotency:
    async def test_replayed_key_returns_the_original_job_without_requeueing(
        self,
        tenant: Tenant,
        redis_client: redis.Redis,
        queue_settings: QueueSettings,
    ) -> None:
        headers = {"Idempotency-Key": "key-1"}
        body = {"message": "hello"}
        url = f"/api/v1/projects/{tenant.project_id}/jobs"

        first = await tenant.client.post(url, json=body, headers=headers)
        second = await tenant.client.post(url, json=body, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        # Exactly one queue message, so the worker cannot run the job twice.
        assert await queued_ids(redis_client, queue_settings.job_queue_key) == [first.json()["id"]]

    async def test_only_one_row_is_created_for_a_replayed_key(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        headers = {"Idempotency-Key": "key-2"}
        for _ in range(5):
            await tenant.client.post(
                f"/api/v1/projects/{tenant.project_id}/jobs",
                json={"message": "hello"},
                headers=headers,
            )

        async with session_factory() as session:
            rows = list((await session.scalars(select(Job))).all())

        assert len(rows) == 1

    async def test_reusing_a_key_with_a_different_body_is_rejected(self, tenant: Tenant) -> None:
        headers = {"Idempotency-Key": "key-3"}
        url = f"/api/v1/projects/{tenant.project_id}/jobs"
        await tenant.client.post(url, json={"message": "first"}, headers=headers)

        response = await tenant.client.post(url, json={"message": "second"}, headers=headers)

        assert response.status_code == 409
        assert response.json()["code"] == "idempotency_key_reused"

    async def test_requests_without_a_key_always_create_new_jobs(self, tenant: Tenant) -> None:
        url = f"/api/v1/projects/{tenant.project_id}/jobs"
        first = await tenant.client.post(url, json={"message": "hello"})
        second = await tenant.client.post(url, json={"message": "hello"})

        assert first.json()["id"] != second.json()["id"]


class TestReadJob:
    async def test_fetches_a_created_job(self, tenant: Tenant) -> None:
        created = (
            await tenant.client.post(
                f"/api/v1/projects/{tenant.project_id}/jobs", json={"message": "hello"}
            )
        ).json()

        response = await tenant.client.get(f"/api/v1/jobs/{created['id']}")

        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    async def test_unknown_job_is_refused_rather_than_reported_missing(
        self, tenant: Tenant
    ) -> None:
        """A 404 here would confirm which identifiers exist."""
        response = await tenant.client.get("/api/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV")

        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"


class TestListJobs:
    async def test_returns_newest_first(self, tenant: Tenant) -> None:
        url = f"/api/v1/projects/{tenant.project_id}/jobs"
        created = [
            (await tenant.client.post(url, json={"message": f"job-{index}"})).json()["id"]
            for index in range(3)
        ]

        response = await tenant.client.get(url)

        assert [item["id"] for item in response.json()["items"]] == list(reversed(created))

    async def test_empty_list_has_no_cursor(self, tenant: Tenant) -> None:
        response = await tenant.client.get(f"/api/v1/projects/{tenant.project_id}/jobs")

        assert response.json() == {"items": [], "next_cursor": None}

    async def test_pagination_walks_every_job_exactly_once(self, tenant: Tenant) -> None:
        url = f"/api/v1/projects/{tenant.project_id}/jobs"
        created = {
            (await tenant.client.post(url, json={"message": f"job-{index}"})).json()["id"]
            for index in range(7)
        }

        seen: list[str] = []
        cursor: str | None = None
        while True:
            params = {"limit": 3} | ({"cursor": cursor} if cursor else {})
            page = (await tenant.client.get(url, params=params)).json()
            seen.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

        assert sorted(seen) == sorted(created)
        assert len(seen) == len(set(seen))
