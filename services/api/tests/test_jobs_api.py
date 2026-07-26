"""Integration tests for the jobs resource against real PostgreSQL and Redis."""

from __future__ import annotations

import httpx
import pytest
import redis.asyncio as redis
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
        client: httpx.AsyncClient,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client: redis.Redis,
        queue_settings: QueueSettings,
    ) -> None:
        response = await client.post("/api/v1/jobs", json={"message": "hello"})

        assert response.status_code == 201
        body = response.json()
        assert is_sortable_id(body["id"])
        assert body["state"] == JobState.PENDING
        assert body["payload"] == {"message": "hello"}
        assert body["result"] is None
        assert body["attempts"] == 0

        async with session_factory() as session:
            stored = await session.get(Job, body["id"])
        assert stored is not None
        assert stored.state == JobState.PENDING

        assert await queued_ids(redis_client, queue_settings.job_queue_key) == [body["id"]]

    async def test_records_the_request_correlation_id_on_the_job(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/api/v1/jobs", json={"message": "hello"}, headers={"x-correlation-id": "cid_web"}
        )

        assert response.json()["correlation_id"] == "cid_web"

    async def test_continues_the_inbound_trace(self, client: httpx.AsyncClient) -> None:
        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

        response = await client.post(
            "/api/v1/jobs", json={"message": "hello"}, headers={"traceparent": traceparent}
        )

        assert response.json()["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"


class TestIdempotency:
    async def test_replayed_key_returns_the_original_job_without_requeueing(
        self,
        client: httpx.AsyncClient,
        redis_client: redis.Redis,
        queue_settings: QueueSettings,
    ) -> None:
        headers = {"Idempotency-Key": "key-1"}
        body = {"message": "hello"}

        first = await client.post("/api/v1/jobs", json=body, headers=headers)
        second = await client.post("/api/v1/jobs", json=body, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        # Exactly one queue message, so the worker cannot run the job twice.
        assert await queued_ids(redis_client, queue_settings.job_queue_key) == [first.json()["id"]]

    async def test_only_one_row_is_created_for_a_replayed_key(
        self, client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        headers = {"Idempotency-Key": "key-2"}
        for _ in range(5):
            await client.post("/api/v1/jobs", json={"message": "hello"}, headers=headers)

        async with session_factory() as session:
            rows = list((await session.scalars(select(Job))).all())

        assert len(rows) == 1

    async def test_reusing_a_key_with_a_different_body_is_rejected(
        self, client: httpx.AsyncClient
    ) -> None:
        headers = {"Idempotency-Key": "key-3"}
        await client.post("/api/v1/jobs", json={"message": "first"}, headers=headers)

        response = await client.post("/api/v1/jobs", json={"message": "second"}, headers=headers)

        assert response.status_code == 409
        assert response.json()["code"] == "idempotency_key_reused"

    async def test_requests_without_a_key_always_create_new_jobs(
        self, client: httpx.AsyncClient
    ) -> None:
        first = await client.post("/api/v1/jobs", json={"message": "hello"})
        second = await client.post("/api/v1/jobs", json={"message": "hello"})

        assert first.json()["id"] != second.json()["id"]


class TestReadJob:
    async def test_fetches_a_created_job(self, client: httpx.AsyncClient) -> None:
        created = (await client.post("/api/v1/jobs", json={"message": "hello"})).json()

        response = await client.get(f"/api/v1/jobs/{created['id']}")

        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    async def test_unknown_job_returns_the_not_found_contract(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/jobs/01ARZ3NDEKTSV4RRFFQ69G5FAV")

        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "not_found"
        assert body["correlation_id"].startswith("cid_")


class TestListJobs:
    async def test_returns_newest_first(self, client: httpx.AsyncClient) -> None:
        created = [
            (await client.post("/api/v1/jobs", json={"message": f"job-{index}"})).json()["id"]
            for index in range(3)
        ]

        response = await client.get("/api/v1/jobs")

        assert [item["id"] for item in response.json()["items"]] == list(reversed(created))

    async def test_empty_list_has_no_cursor(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/jobs")

        assert response.json() == {"items": [], "next_cursor": None}

    async def test_pagination_walks_every_job_exactly_once(self, client: httpx.AsyncClient) -> None:
        created = {
            (await client.post("/api/v1/jobs", json={"message": f"job-{index}"})).json()["id"]
            for index in range(7)
        }

        seen: list[str] = []
        cursor: str | None = None
        while True:
            params = {"limit": 3} | ({"cursor": cursor} if cursor else {})
            page = (await client.get("/api/v1/jobs", params=params)).json()
            seen.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

        assert sorted(seen) == sorted(created)
        assert len(seen) == len(set(seen))
