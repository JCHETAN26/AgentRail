"""Release policy and gate endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status

from agentrail_api.dependencies import ActorDep, CheckRunPublisherDep, SessionDep, SettingsDep
from agentrail_api.execution import service as execution_service
from agentrail_api.identity import service as identity_service
from agentrail_api.release import service
from agentrail_api.release.schemas import (
    CreateReleasePolicyRequest,
    CreateRepositoryBindingRequest,
    EvaluateGateRequest,
    GateEvaluationListResponse,
    GateEvaluationResponse,
    ReleasePolicyListResponse,
    ReleasePolicyResponse,
    RepositoryBindingListResponse,
    RepositoryBindingResponse,
)
from agentrail_core.errors import ProblemDetail

router = APIRouter(prefix="/api/v1", tags=["release"])

ProjectId = Annotated[str, Path(min_length=26, max_length=26)]
RunId = Annotated[str, Path(min_length=26, max_length=26)]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
    409: {"model": ProblemDetail, "description": "Duplicate policy, or no report to gate yet."},
    422: {"model": ProblemDetail, "description": "Validation failed."},
}


@router.post(
    "/projects/{project_id}/release-policies",
    response_model=ReleasePolicyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an immutable release policy version",
    responses=_ERRORS,
)
async def create_release_policy(
    project_id: ProjectId,
    body: CreateReleasePolicyRequest,
    actor: ActorDep,
    session: SessionDep,
) -> ReleasePolicyResponse:
    principal, _project = await identity_service.resolve_project(session, actor, project_id)
    policy = await service.create_policy(
        session, actor, principal, project_id=project_id, request=body
    )
    await session.commit()
    await session.refresh(policy)
    return ReleasePolicyResponse.model_validate(policy)


@router.get(
    "/projects/{project_id}/release-policies",
    response_model=ReleasePolicyListResponse,
    summary="List release policies",
    responses=_ERRORS,
)
async def list_release_policies(
    project_id: ProjectId, actor: ActorDep, session: SessionDep
) -> ReleasePolicyListResponse:
    principal, _project = await identity_service.resolve_project(session, actor, project_id)
    policies = await service.list_policies(session, principal, project_id=project_id)
    return ReleasePolicyListResponse(
        items=[ReleasePolicyResponse.model_validate(policy) for policy in policies]
    )


@router.post(
    "/evaluation-runs/{run_id}/gate",
    response_model=GateEvaluationResponse,
    summary="Judge a run against a release policy",
    responses=_ERRORS,
)
async def evaluate_gate(
    run_id: RunId,
    body: EvaluateGateRequest,
    actor: ActorDep,
    session: SessionDep,
    publisher: CheckRunPublisherDep,
    settings: SettingsDep,
) -> GateEvaluationResponse:
    principal = await execution_service.principal_for_run(session, actor, run_id)
    evaluation = await service.evaluate_run_gate(
        session,
        actor,
        principal,
        run_id=run_id,
        request=body,
        publisher=publisher,
        web_base_url=settings.web_base_url,
    )
    await session.commit()
    await session.refresh(evaluation)
    return GateEvaluationResponse.model_validate(evaluation)


@router.get(
    "/evaluation-runs/{run_id}/gate",
    response_model=GateEvaluationListResponse,
    summary="List gate verdicts recorded for a run",
    responses=_ERRORS,
)
async def list_gate_evaluations(
    run_id: RunId, actor: ActorDep, session: SessionDep
) -> GateEvaluationListResponse:
    principal = await execution_service.principal_for_run(session, actor, run_id)
    evaluations = await service.list_run_gate_evaluations(session, principal, run_id=run_id)
    return GateEvaluationListResponse(
        items=[GateEvaluationResponse.model_validate(item) for item in evaluations]
    )


@router.post(
    "/projects/{project_id}/github-repositories",
    response_model=RepositoryBindingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Claim a GitHub repository for this project",
    responses=_ERRORS,
)
async def create_repository_binding(
    project_id: ProjectId,
    body: CreateRepositoryBindingRequest,
    actor: ActorDep,
    session: SessionDep,
) -> RepositoryBindingResponse:
    principal, _project = await identity_service.resolve_project(session, actor, project_id)
    binding = await service.create_repository_binding(
        session,
        actor,
        principal,
        project_id=project_id,
        owner=body.owner,
        repository=body.repository,
    )
    await session.commit()
    await session.refresh(binding)
    return RepositoryBindingResponse.model_validate(binding)


@router.get(
    "/projects/{project_id}/github-repositories",
    response_model=RepositoryBindingListResponse,
    summary="List the GitHub repositories bound to this project",
    responses=_ERRORS,
)
async def list_repository_bindings(
    project_id: ProjectId, actor: ActorDep, session: SessionDep
) -> RepositoryBindingListResponse:
    principal, _project = await identity_service.resolve_project(session, actor, project_id)
    bindings = await service.list_repository_bindings(session, principal, project_id=project_id)
    return RepositoryBindingListResponse(
        items=[RepositoryBindingResponse.model_validate(item) for item in bindings]
    )
