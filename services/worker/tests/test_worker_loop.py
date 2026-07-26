"""End-to-end tests for the worker process itself.

Real Redis, real PostgreSQL, and a real sandbox served over a real socket. The
only thing these do not exercise is container packaging.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import redis.asyncio as redis
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from worker_test_support import JobFactory

from agentrail_cloudops_sandbox.app import SandboxSettings
from agentrail_cloudops_sandbox.app import create_app as create_sandbox_app
from agentrail_core.jobs import Job, JobState
from agentrail_core.queue import publish_job, queue_depth
from agentrail_core.settings import QueueSettings
from agentrail_worker.settings import WorkerSettings
from agentrail_worker.worker import Worker, _EmbeddedServer

pytestmark = pytest.mark.integration

POLL_INTERVAL = 0.02
POLL_TIMEOUT = 10.0


@pytest.fixture
async def sandbox_url() -> AsyncIterator[str]:
    """The real sandbox app on an ephemeral port."""
    config = uvicorn.Config(
        create_sandbox_app(SandboxSettings(_env_file=None, environment="test")),
        host="127.0.0.1",
        port=0,
        log_config=None,
        access_log=False,
    )
    server = _EmbeddedServer(config)
    task = asyncio.create_task(server.serve())
    for _ in range(int(POLL_TIMEOUT / POLL_INTERVAL)):
        if server.started:
            break
        await asyncio.sleep(POLL_INTERVAL)
    else:
        raise AssertionError("the sandbox server did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


def free_port() -> int:
    """Ask the OS for an unused port so parallel test runs do not collide."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def worker_settings(queue_settings: QueueSettings, sandbox_url: str) -> WorkerSettings:
    return WorkerSettings(
        service_name="agentrail-worker-tests",
        worker_id="loop-worker",
        job_queue_key=queue_settings.job_queue_key,
        sandbox_base_url=sandbox_url,
        health_port=free_port(),
        queue_block_timeout_seconds=1,
        recovery_sweep_interval_seconds=0.05,
        stale_pending_seconds=1.0,
    )


async def wait_for_state(
    factory: async_sessionmaker[AsyncSession], job_id: str, state: JobState
) -> Job:
    deadline = asyncio.get_running_loop().time() + POLL_TIMEOUT
    while asyncio.get_running_loop().time() < deadline:
        async with factory() as session:
            job = await session.get(Job, job_id)
            if job is not None and job.state == state:
                return job
        await asyncio.sleep(POLL_INTERVAL)
    raise AssertionError(f"job {job_id} did not reach {state} within {POLL_TIMEOUT}s")


class TestConsumeLoop:
    async def test_consumes_a_published_job_and_completes_it(
        self,
        worker_settings: WorkerSettings,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client: redis.Redis,
        make_job: JobFactory,
    ) -> None:
        job_id = await make_job(message="through the queue")
        await publish_job(redis_client, worker_settings.job_queue_key, job_id)

        worker = Worker(worker_settings)
        run_task = asyncio.create_task(worker.run())
        try:
            job = await wait_for_state(session_factory, job_id, JobState.COMPLETED)
        finally:
            worker.request_stop()
            await run_task

        assert job.result == {
            "echo": "through the queue",
            "digest": "a4d621c36256a2e1",
            "sandbox_version": "0.1.0",
        }
        assert job.worker_id == "loop-worker"
        assert await queue_depth(redis_client, worker_settings.job_queue_key) == 0

    async def test_stops_cleanly_on_request_and_leaves_no_job_half_written(
        self,
        worker_settings: WorkerSettings,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client: redis.Redis,
        make_job: JobFactory,
    ) -> None:
        job_id = await make_job()
        await publish_job(redis_client, worker_settings.job_queue_key, job_id)

        worker = Worker(worker_settings)
        run_task = asyncio.create_task(worker.run())
        await wait_for_state(session_factory, job_id, JobState.COMPLETED)

        worker.request_stop()
        await asyncio.wait_for(run_task, timeout=POLL_TIMEOUT)

        assert run_task.exception() is None
        async with session_factory() as session:
            job = await session.get(Job, job_id)
        assert job is not None
        assert job.state == JobState.COMPLETED

    async def test_a_message_for_a_missing_job_is_dropped_without_stalling_the_loop(
        self,
        worker_settings: WorkerSettings,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client: redis.Redis,
        make_job: JobFactory,
    ) -> None:
        await publish_job(redis_client, worker_settings.job_queue_key, "01ARZ3NDEKTSV4RRFFQ69G5FAV")
        real_job_id = await make_job()
        await publish_job(redis_client, worker_settings.job_queue_key, real_job_id)

        worker = Worker(worker_settings)
        run_task = asyncio.create_task(worker.run())
        try:
            await wait_for_state(session_factory, real_job_id, JobState.COMPLETED)
        finally:
            worker.request_stop()
            await run_task


class TestRecoverySweep:
    async def test_requeues_a_job_stranded_in_pending(
        self,
        worker_settings: WorkerSettings,
        redis_client: redis.Redis,
        make_job: JobFactory,
    ) -> None:
        """Covers the crash window between committing the row and publishing it."""
        stale_at = datetime.now(UTC) - timedelta(seconds=60)
        await make_job(created_at=stale_at)
        worker = Worker(worker_settings)

        try:
            requeued = await worker._requeue_stale_pending()
        finally:
            await worker.aclose()

        assert requeued == 1
        assert await queue_depth(redis_client, worker_settings.job_queue_key) == 1

    async def test_ignores_a_recently_created_pending_job(
        self,
        worker_settings: WorkerSettings,
        redis_client: redis.Redis,
        make_job: JobFactory,
    ) -> None:
        await make_job()
        worker = Worker(worker_settings)

        try:
            requeued = await worker._requeue_stale_pending()
        finally:
            await worker.aclose()

        assert requeued == 0
        assert await queue_depth(redis_client, worker_settings.job_queue_key) == 0

    async def test_requeued_job_is_executed_exactly_once(
        self,
        worker_settings: WorkerSettings,
        session_factory: async_sessionmaker[AsyncSession],
        make_job: JobFactory,
    ) -> None:
        stale_at = datetime.now(UTC) - timedelta(seconds=60)
        job_id = await make_job(created_at=stale_at)

        worker = Worker(worker_settings)
        run_task = asyncio.create_task(worker.run())
        try:
            job = await wait_for_state(session_factory, job_id, JobState.COMPLETED)
        finally:
            worker.request_stop()
            await run_task

        assert job.attempts == 1


class TestWorkerHealthSurface:
    async def test_readyz_reports_all_three_dependencies(
        self, worker_settings: WorkerSettings, db_engine: object, redis_client: redis.Redis
    ) -> None:
        from agentrail_worker.health_app import create_health_app

        worker = Worker(worker_settings)
        app = create_health_app(
            service_name=worker_settings.service_name,
            engine=worker._engine,
            redis_client=worker._redis,
            sandbox=worker._sandbox,
        )
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://worker") as client:
                response = await client.get("/readyz")
        finally:
            await worker.aclose()

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert {item["name"] for item in body["dependencies"]} == {
            "postgresql",
            "redis",
            "cloudops_sandbox",
        }
