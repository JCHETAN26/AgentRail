"""The jobs resource, scoped to a project.

Jobs no longer live at the root: every path names the project, and the project
names the organisation, so there is no way to reach a job without first passing
a tenancy check.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Response, status

from agentrail_api.dependencies import ActorDep, ContextDep, RedisDep, SessionDep, SettingsDep
from agentrail_api.identity import service as identity_service
from agentrail_api.jobs import service
from agentrail_api.jobs.schemas import CreateJobRequest, JobListResponse, JobResponse
from agentrail_core.errors import ErrorCode, ProblemDetail
from agentrail_core.identity import Permission, authorize
from agentrail_core.logging import get_logger
from agentrail_core.queue import publish_job

router = APIRouter(prefix="/api/v1", tags=["jobs"])
logger = get_logger(__name__)

ProjectId = Annotated[str, Path(min_length=26, max_length=26)]
JobId = Annotated[str, Path(min_length=26, max_length=26)]

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
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
            "instead of creating a second one. Scoped to the project."
        ),
    ),
]


@router.post(
    "/projects/{project_id}/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a job in a project",
    responses=_ERROR_RESPONSES,
)
async def create_job(
    project_id: ProjectId,
    request: CreateJobRequest,
    response: Response,
    actor: ActorDep,
    session: SessionDep,
    redis_client: RedisDep,
    settings: SettingsDep,
    context: ContextDep,
    idempotency_key: IdempotencyKeyHeader = None,
) -> JobResponse:
    principal, project = await identity_service.resolve_project(session, actor, project_id)
    authorize(principal, Permission.JOB_CREATE, organisation_id=project.organisation_id)

    job, created = await service.create_job(
        session, request, context, project_id=project.id, idempotency_key=idempotency_key
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
    logger.info(
        "job_created",
        extra={"job_id": job.id, "kind": job.kind, "project_id": project.id},
    )
    return JobResponse.model_validate(job)


@router.get(
    "/projects/{project_id}/jobs",
    response_model=JobListResponse,
    summary="List a project's jobs, newest first",
    responses=_ERROR_RESPONSES,
)
async def list_jobs(
    project_id: ProjectId,
    actor: ActorDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = 20,
    cursor: Annotated[str | None, Query(max_length=26)] = None,
) -> JobListResponse:
    principal, project = await identity_service.resolve_project(session, actor, project_id)
    authorize(principal, Permission.JOB_READ, organisation_id=project.organisation_id)

    jobs, next_cursor = await service.list_jobs(
        session, project_id=project.id, limit=limit, cursor=cursor
    )
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
async def get_job(job_id: JobId, actor: ActorDep, session: SessionDep) -> JobResponse:
    """Fetch by identifier alone.

    The job's project decides the tenancy check, and a job belonging to another
    tenant is reported as forbidden rather than missing — identical to a job
    that does not exist, so identifiers cannot be probed.
    """
    job = await service.get_job_unscoped(session, job_id)
    principal, project = await identity_service.resolve_project(session, actor, job.project_id)
    authorize(principal, Permission.JOB_READ, organisation_id=project.organisation_id)
    return JobResponse.model_validate(job)


__all__ = ["ErrorCode", "router"]
