"""Job creation and retrieval.

Ordering matters here and is the reason this is a service rather than inline
route code:

1. the job row is committed to PostgreSQL;
2. *then* the identifier is published to Redis.

If step 2 fails the job is still durably recorded as ``PENDING`` and the
worker's recovery sweep will pick it up. The reverse order would allow a worker
to dequeue an identifier for a row that does not exist yet — or never will.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.jobs.schemas import CreateJobRequest
from agentrail_core.correlation import CorrelationContext
from agentrail_core.errors import IdempotencyKeyReusedError, NotFoundError
from agentrail_core.ids import new_sortable_id
from agentrail_core.jobs import Job, JobState

MAX_PAGE_SIZE = 100


def request_fingerprint(request: CreateJobRequest) -> str:
    """Stable hash of the request body, used to police idempotency-key reuse."""
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def get_job(session: AsyncSession, job_id: str) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job not found", details={"job_id": job_id})
    return job


async def list_jobs(
    session: AsyncSession, *, limit: int = 20, cursor: str | None = None
) -> tuple[list[Job], str | None]:
    """Return a page of jobs, newest first, using keyset pagination.

    Job identifiers are ULIDs, so the primary key is already in creation order
    and no secondary sort column is needed.
    """
    bounded = max(1, min(limit, MAX_PAGE_SIZE))
    statement = select(Job).order_by(Job.id.desc()).limit(bounded + 1)
    if cursor is not None:
        statement = statement.where(Job.id < cursor)

    rows = list((await session.scalars(statement)).all())
    if len(rows) > bounded:
        page = rows[:bounded]
        return page, page[-1].id
    return rows, None


async def _find_by_idempotency_key(session: AsyncSession, key: str) -> Job | None:
    job: Job | None = await session.scalar(select(Job).where(Job.idempotency_key == key))
    return job


async def create_job(
    session: AsyncSession,
    request: CreateJobRequest,
    context: CorrelationContext,
    *,
    idempotency_key: str | None = None,
) -> tuple[Job, bool]:
    """Create a job, or return the existing one for a replayed idempotency key.

    Returns ``(job, created)``. ``created`` is ``False`` when an earlier
    identical request already produced this job, which lets the caller answer
    ``200`` instead of ``201`` and skip re-publishing.
    """
    fingerprint = request_fingerprint(request)

    if idempotency_key is not None:
        existing = await _find_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return _assert_same_request(existing, fingerprint, idempotency_key), False

    job = Job(
        id=new_sortable_id(),
        kind=request.kind.value,
        state=JobState.PENDING,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        correlation_id=context.correlation_id,
        trace_id=context.trace_id,
        payload={"message": request.message},
        attempts=0,
        version=1,
    )
    session.add(job)

    try:
        await session.flush()
    except IntegrityError:
        # Two concurrent requests raced on the same idempotency key. The other
        # one won; adopt its row rather than failing the caller.
        await session.rollback()
        if idempotency_key is None:
            raise
        winner = await _find_by_idempotency_key(session, idempotency_key)
        if winner is None:  # pragma: no cover - only reachable on an unrelated conflict
            raise
        return _assert_same_request(winner, fingerprint, idempotency_key), False

    return job, True


def _assert_same_request(job: Job, fingerprint: str, idempotency_key: str) -> Job:
    if job.request_fingerprint != fingerprint:
        raise IdempotencyKeyReusedError(
            "This idempotency key was already used with a different request body.",
            details={"idempotency_key": idempotency_key, "job_id": job.id},
        )
    return job
