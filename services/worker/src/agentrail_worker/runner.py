"""Claim, execute and complete a single job.

Delivery from Redis is at-least-once, so this module assumes every job
identifier may arrive more than once, out of order, or for a job another worker
is already running. Safety comes from two layers:

* the domain state machine in ``agentrail_core.jobs.state`` declares which
  transitions exist at all;
* every write is a *conditional* ``UPDATE ... WHERE state = <expected>``, so two
  workers racing on the same identifier cannot both win. The loser observes zero
  updated rows and drops the message.

This is what makes duplicate delivery harmless without a distributed lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.correlation import CorrelationContext, correlation_scope, new_span_id
from agentrail_core.errors import PlatformError
from agentrail_core.jobs import Job, JobState, assert_transition
from agentrail_core.logging import get_logger
from agentrail_worker.sandbox_client import SandboxClient

logger = get_logger(__name__)


class JobOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    #: The identifier referred to a job that was not PENDING — already claimed by
    #: another worker, or already finished. A duplicate delivery, not an error.
    SKIPPED = "skipped"
    #: The identifier referred to no row at all.
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    kind: str
    payload: dict[str, Any]
    correlation_id: str
    trace_id: str
    attempts: int

    def correlation_context(self) -> CorrelationContext:
        """Rebuild the originating trace context, starting a new span for this hop."""
        return CorrelationContext(
            correlation_id=self.correlation_id,
            trace_id=self.trace_id,
            span_id=new_span_id(),
        )


class UnsupportedJobKindError(PlatformError):
    """The worker does not know how to execute this job kind."""


class JobRunner:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sandbox: SandboxClient,
        *,
        worker_id: str,
    ) -> None:
        self._session_factory = session_factory
        self._sandbox = sandbox
        self._worker_id = worker_id

    async def process(self, job_id: str) -> JobOutcome:
        """Run one job end to end. Never raises for expected failure modes."""
        claimed = await self._claim(job_id)
        if claimed is None:
            return await self._classify_unclaimable(job_id)

        with correlation_scope(claimed.correlation_context()) as context:
            logger.info(
                "job_claimed",
                extra={"job_id": claimed.id, "kind": claimed.kind, "attempt": claimed.attempts},
            )
            try:
                result = await self._execute(claimed, context)
            except PlatformError as exc:
                await self._fail(claimed.id, code=exc.code.value, message=exc.message)
                logger.warning(
                    "job_failed", extra={"job_id": claimed.id, "error_code": exc.code.value}
                )
                return JobOutcome.FAILED
            except Exception as exc:
                await self._fail(claimed.id, code="internal_error", message=f"{type(exc).__name__}")
                logger.exception("job_errored", extra={"job_id": claimed.id})
                return JobOutcome.FAILED

            completed = await self._complete(claimed.id, result)
            if not completed:
                # Another actor moved the job to a terminal state first.
                logger.warning("job_completion_ignored", extra={"job_id": claimed.id})
                return JobOutcome.SKIPPED
            logger.info("job_completed", extra={"job_id": claimed.id})
            return JobOutcome.COMPLETED

    async def _execute(self, job: ClaimedJob, context: CorrelationContext) -> dict[str, Any]:
        if job.kind != "noop":
            raise UnsupportedJobKindError(
                f"Unsupported job kind: {job.kind}", details={"kind": job.kind}
            )
        message = job.payload.get("message")
        if not isinstance(message, str):
            raise UnsupportedJobKindError(
                "Job payload is missing a string 'message'", details={"job_id": job.id}
            )
        return await self._sandbox.execute_noop(message, context)

    async def _claim(self, job_id: str) -> ClaimedJob | None:
        """Atomically move PENDING → RUNNING, returning the claimed row."""
        assert_transition(JobState.PENDING, JobState.RUNNING)

        statement = (
            update(Job)
            .where(Job.id == job_id, Job.state == JobState.PENDING)
            .values(
                state=JobState.RUNNING,
                attempts=Job.attempts + 1,
                worker_id=self._worker_id,
                started_at=func.now(),
                updated_at=func.now(),
                version=Job.version + 1,
            )
            .returning(
                Job.id, Job.kind, Job.payload, Job.correlation_id, Job.trace_id, Job.attempts
            )
        )
        async with self._session_factory() as session:
            row = (await session.execute(statement)).one_or_none()
            await session.commit()

        if row is None:
            return None
        return ClaimedJob(
            id=row.id,
            kind=row.kind,
            payload=row.payload,
            correlation_id=row.correlation_id,
            trace_id=row.trace_id,
            attempts=row.attempts,
        )

    async def _classify_unclaimable(self, job_id: str) -> JobOutcome:
        async with self._session_factory() as session:
            exists = await session.scalar(select(Job.id).where(Job.id == job_id))
        if exists is None:
            logger.warning("job_missing", extra={"job_id": job_id})
            return JobOutcome.MISSING
        logger.info("job_already_handled", extra={"job_id": job_id})
        return JobOutcome.SKIPPED

    async def _complete(self, job_id: str, result: dict[str, Any]) -> bool:
        assert_transition(JobState.RUNNING, JobState.COMPLETED)

        statement = (
            update(Job)
            .where(Job.id == job_id, Job.state == JobState.RUNNING)
            .values(
                state=JobState.COMPLETED,
                result=result,
                completed_at=func.now(),
                updated_at=func.now(),
                version=Job.version + 1,
            )
        )
        async with self._session_factory() as session:
            updated = (await session.execute(statement)).rowcount
            await session.commit()
        return bool(updated)

    async def _fail(self, job_id: str, *, code: str, message: str) -> bool:
        assert_transition(JobState.RUNNING, JobState.FAILED)

        statement = (
            update(Job)
            .where(Job.id == job_id, Job.state == JobState.RUNNING)
            .values(
                state=JobState.FAILED,
                error_code=code[:64],
                error_message=message[:1024],
                completed_at=func.now(),
                updated_at=func.now(),
                version=Job.version + 1,
            )
        )
        async with self._session_factory() as session:
            updated = (await session.execute(statement)).rowcount
            await session.commit()
        return bool(updated)
