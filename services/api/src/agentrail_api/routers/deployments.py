"""Deployment endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status

from agentrail_api.dependencies import ActorDep, SessionDep
from agentrail_api.deployments import service
from agentrail_api.deployments.schemas import (
    CreateDeploymentRequest,
    DeploymentListResponse,
    DeploymentResponse,
    RollbackDeploymentRequest,
)
from agentrail_api.identity import service as identity_service
from agentrail_core.errors import ProblemDetail

router = APIRouter(prefix="/api/v1", tags=["deployments"])

ProjectId = Annotated[str, Path(min_length=26, max_length=26)]
DeploymentId = Annotated[str, Path(min_length=26, max_length=26)]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
    409: {"model": ProblemDetail, "description": "Gate missing, blocked, or invalid transition."},
    422: {"model": ProblemDetail, "description": "Validation failed."},
}


@router.post(
    "/deployments",
    response_model=DeploymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Deploy a passing run to canary",
    responses=_ERRORS,
)
async def create_deployment(
    body: CreateDeploymentRequest, actor: ActorDep, session: SessionDep
) -> DeploymentResponse:
    principal = await service.principal_for_run(session, actor, body.run_id)
    deployment = await service.create_deployment(session, actor, principal, request=body)
    await session.commit()
    await session.refresh(deployment)
    return DeploymentResponse.model_validate(deployment)


@router.post(
    "/deployments/{deployment_id}/promote",
    response_model=DeploymentResponse,
    summary="Promote a canary deployment",
    responses=_ERRORS,
)
async def promote_deployment(
    deployment_id: DeploymentId, actor: ActorDep, session: SessionDep
) -> DeploymentResponse:
    principal = await service.principal_for_deployment(session, actor, deployment_id)
    deployment = await service.promote_deployment(
        session, actor, principal, deployment_id=deployment_id
    )
    await session.commit()
    await session.refresh(deployment)
    return DeploymentResponse.model_validate(deployment)


@router.post(
    "/deployments/{deployment_id}/rollback",
    response_model=DeploymentResponse,
    summary="Rollback a deployment",
    responses=_ERRORS,
)
async def rollback_deployment(
    deployment_id: DeploymentId,
    body: RollbackDeploymentRequest,
    actor: ActorDep,
    session: SessionDep,
) -> DeploymentResponse:
    principal = await service.principal_for_deployment(session, actor, deployment_id)
    deployment = await service.rollback_deployment(
        session, actor, principal, deployment_id=deployment_id, reason=body.reason
    )
    await session.commit()
    await session.refresh(deployment)
    return DeploymentResponse.model_validate(deployment)


@router.get(
    "/projects/{project_id}/deployments",
    response_model=DeploymentListResponse,
    summary="List deployment history",
    responses=_ERRORS,
)
async def list_deployments(
    project_id: ProjectId, actor: ActorDep, session: SessionDep
) -> DeploymentListResponse:
    principal, _project = await identity_service.resolve_project(session, actor, project_id)
    deployments = await service.list_deployments(session, principal, project_id=project_id)
    return DeploymentListResponse(
        items=[DeploymentResponse.model_validate(item) for item in deployments]
    )
