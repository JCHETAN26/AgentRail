"""The jobs resource — the Phase 0 vertical slice."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Response, status

from agentrail_api.dependencies import ContextDep, RedisDep, SessionDep, SettingsDep
from agentrail_api.jobs import service
from agentrail_api.jobs.schemas import CreateJobRequest, JobListResponse, JobResponse
from agentrail_core.errors import ErrorCode, ProblemDetail
from agentrail_core.logging import get_logger
from agentrail_core.queue import publish_job

router = APIRouter(prefix="/api/v1", tags=["jobs"])
logger = get_logger(__name__)

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"model": ProblemDetail, "description": "No such job."},
    409: {"model": ProblemDetail, "description": "Idempotency key reused with a different body."},
    422: {"model": ProblemDetail, "description": "The request failed validation."},
    503: {"model": ProblemDetail, "description": "A required dependency is unavailable."},
}

IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        max_length=128,
        description=(
            "Optional. Replaying a request with the same key returns the original job "
            "instead of creating a second one."
        ),
    ),
]


@router.post(
    "/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a job",
    responses=_ERROR_RESPONSES,
)
async def create_job(
    request: CreateJobRequest,
    response: Response,
    session: SessionDep,
    redis_client: RedisDep,
    settings: SettingsDep,
    context: ContextDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> JobResponse:
    job, created = await service.create_job(
        session, request, context, idempotency_key=idempotency_key
    )
    await session.commit()
    await session.refresh(job)

    if not created:
        # A replayed idempotency key. The original job is already queued.
        response.status_code = status.HTTP_200_OK
        logger.info("job_create_replayed", extra={"job_id": job.id})
        return JobResponse.model_validate(job)

    # Published only after the row is durable, so a consumer can never observe a
    # job identifier that PostgreSQL has not committed.
    await publish_job(redis_client, settings.job_queue_key, job.id)
    logger.info("job_created", extra={"job_id": job.id, "kind": job.kind})
    return JobResponse.model_validate(job)


@router.get(
    "/jobs",
    response_model=JobListResponse,
    summary="List jobs, newest first",
    responses={503: _ERROR_RESPONSES[503]},
)
async def list_jobs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = 20,
    cursor: Annotated[str | None, Query(max_length=26)] = None,
) -> JobListResponse:
    jobs, next_cursor = await service.list_jobs(session, limit=limit, cursor=cursor)
    return JobListResponse(
        items=[JobResponse.model_validate(job) for job in jobs],
        next_cursor=next_cursor,
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="Fetch a single job",
    responses=_ERROR_RESPONSES,
)
async def get_job(
    session: SessionDep,
    job_id: Annotated[str, Path(min_length=26, max_length=26)],
) -> JobResponse:
    job = await service.get_job(session, job_id)
    return JobResponse.model_validate(job)


__all__ = ["ErrorCode", "router"]
