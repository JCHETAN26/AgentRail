"""The worker process: consume loop, recovery sweep and graceful shutdown.

Shutdown contract: on SIGTERM the worker stops pulling new identifiers, finishes
the job already in flight, and only then closes its connections. A job is never
abandoned half-written, because the durable transition to a terminal state
happens inside :class:`~agentrail_worker.runner.JobRunner` before the loop is
allowed to exit.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import uvicorn
from sqlalchemy import select

from agentrail_core.db import create_database_engine, create_session_factory
from agentrail_core.jobs import Job, JobState
from agentrail_core.logging import configure_logging, get_logger
from agentrail_core.queue import create_redis_client, publish_job
from agentrail_worker.health_app import create_health_app
from agentrail_worker.run_runner import (
    EvaluationRunRunner,
    RunOutcome,
    mark_outbox_published,
    pending_outbox_run_ids,
)
from agentrail_worker.runner import JobOutcome, JobRunner
from agentrail_worker.sandbox_client import SandboxClient
from agentrail_worker.settings import WorkerSettings

logger = get_logger(__name__)


class _EmbeddedServer(uvicorn.Server):
    """A uvicorn server that does not touch process signal handlers.

    ``uvicorn.Server.serve`` installs its own SIGINT/SIGTERM handlers, which
    would replace the worker's. The worker owns shutdown: it stops the consume
    loop first and only then asks this server to exit.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


class Worker:
    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._stop = asyncio.Event()
        self._engine = create_database_engine(settings)
        self._session_factory = create_session_factory(self._engine)
        self._redis = create_redis_client(settings)
        self._sandbox = SandboxClient(
            str(settings.sandbox_base_url), timeout_seconds=settings.sandbox_timeout_seconds
        )
        self._runner = JobRunner(self._session_factory, self._sandbox, worker_id=settings.worker_id)
        self._run_runner = EvaluationRunRunner(
            self._session_factory,
            worker_id=settings.worker_id,
            lease_seconds=settings.run_item_lease_seconds,
        )

    def request_stop(self) -> None:
        if not self._stop.is_set():
            logger.info("worker_shutdown_requested", extra={"worker_id": self._settings.worker_id})
            self._stop.set()

    async def run(self) -> None:
        health_server = self._build_health_server()
        health_task = asyncio.create_task(health_server.serve(), name="worker-health")
        sweep_task = asyncio.create_task(self._recovery_loop(), name="worker-recovery-sweep")
        logger.info("worker_started", extra={"worker_id": self._settings.worker_id})

        try:
            await self._consume_loop()
        finally:
            sweep_task.cancel()
            health_server.should_exit = True
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task
            with contextlib.suppress(asyncio.CancelledError):
                await health_task
            await self.aclose()
            logger.info("worker_stopped", extra={"worker_id": self._settings.worker_id})

    async def aclose(self) -> None:
        await self._sandbox.aclose()
        await self._redis.aclose()
        await self._engine.dispose()

    def _build_health_server(self) -> uvicorn.Server:
        app = create_health_app(
            service_name=self._settings.service_name,
            engine=self._engine,
            redis_client=self._redis,
            sandbox=self._sandbox,
        )
        config = uvicorn.Config(
            app,
            host="0.0.0.0",  # noqa: S104 - containers must bind all interfaces
            port=self._settings.health_port,
            log_config=None,
            access_log=False,
        )
        return _EmbeddedServer(config)

    async def _consume_loop(self) -> None:
        while not self._stop.is_set():
            work = await self._consume_work()
            if work is None:
                continue  # Idle timeout; re-check the stop flag.
            queue_key, identifier = work

            if queue_key == self._settings.job_queue_key:
                job_outcome = await self._runner.process(identifier)
                if job_outcome is JobOutcome.MISSING:
                    continue
            else:
                run_outcome = await self._run_runner.process(identifier)
                if run_outcome is RunOutcome.MISSING:
                    continue

    async def _consume_work(self) -> tuple[str, str] | None:
        result = await self._redis.blpop(  # type: ignore[misc]
            [self._settings.job_queue_key, self._settings.run_queue_key],
            timeout=self._settings.queue_block_timeout_seconds,
        )
        if result is None:
            return None
        queue_key, identifier = result
        return str(queue_key), str(identifier)

    async def _recovery_loop(self) -> None:
        """Re-publish jobs stranded in PENDING.

        The API publishes to Redis only after committing the row, so a crash
        between those two steps leaves a durable job with no queue message. This
        sweep is the safety net. Re-publishing an already-claimed job is harmless
        because the claim is a conditional update.
        """
        while not self._stop.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._settings.recovery_sweep_interval_seconds
                )
            if self._stop.is_set():
                return
            try:
                requeued = await self._recover_pending_work()
            except Exception:
                logger.exception("recovery_sweep_failed")
                continue
            if requeued:
                logger.info("recovery_sweep_requeued", extra={"job_count": requeued})

    async def _requeue_stale_pending(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=self._settings.stale_pending_seconds)
        statement = (
            select(Job.id)
            .where(Job.state == JobState.PENDING, Job.created_at < cutoff)
            .order_by(Job.created_at)
            .limit(self._settings.recovery_sweep_batch_size)
        )
        async with self._session_factory() as session:
            job_ids = list((await session.scalars(statement)).all())

        for job_id in job_ids:
            await publish_job(self._redis, self._settings.job_queue_key, job_id)
        return len(job_ids)

    async def _recover_pending_work(self) -> int:
        requeued = await self._requeue_stale_pending()
        requeued += await self._publish_pending_outbox()
        recovered_leases = await EvaluationRunRunner.recover_expired_leases(
            self._session_factory, now=datetime.now(UTC)
        )
        requeued += await self._requeue_recoverable_runs()
        if recovered_leases:
            logger.info("evaluation_run_leases_recovered", extra={"item_count": recovered_leases})
        return requeued

    async def _publish_pending_outbox(self) -> int:
        pending = await pending_outbox_run_ids(
            self._session_factory, limit=self._settings.recovery_sweep_batch_size
        )
        for event_id, run_id in pending:
            await publish_job(self._redis, self._settings.run_queue_key, run_id)
            await mark_outbox_published(self._session_factory, event_id=event_id)
        return len(pending)

    async def _requeue_recoverable_runs(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=self._settings.stale_pending_seconds)
        run_ids = await EvaluationRunRunner.recoverable_run_ids(
            self._session_factory, before=cutoff, limit=self._settings.recovery_sweep_batch_size
        )
        for run_id in run_ids:
            await publish_job(self._redis, self._settings.run_queue_key, run_id)
        return len(run_ids)


async def run_worker(settings: WorkerSettings) -> None:
    configure_logging(
        service=settings.service_name,
        environment=settings.environment.value,
        level=settings.log_level,
    )
    worker = Worker(settings)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.request_stop)

    await worker.run()
