"""Human approval endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from agentrail_api.approvals import service
from agentrail_api.approvals.schemas import (
    ApprovalListResponse,
    ApprovalResponse,
    DecideApprovalRequest,
)
from agentrail_api.dependencies import ActorDep, SessionDep
from agentrail_api.execution import service as execution_service
from agentrail_api.identity import service as identity_service
from agentrail_core.approvals import ApprovalState
from agentrail_core.errors import ProblemDetail

router = APIRouter(prefix="/api/v1", tags=["approvals"])

RunId = Annotated[str, Path(min_length=26, max_length=26)]
ApprovalId = Annotated[str, Path(min_length=26, max_length=26)]
ProjectId = Annotated[str, Path(min_length=26, max_length=26)]
ApprovalStateFilter = Annotated[ApprovalState | None, Query()]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
    409: {"model": ProblemDetail, "description": "Already decided."},
    422: {"model": ProblemDetail, "description": "Validation failed."},
}


@router.get(
    "/evaluation-runs/{run_id}/approvals",
    response_model=ApprovalListResponse,
    summary="List approval requests raised by a run",
    responses=_ERRORS,
)
async def list_run_approvals(
    run_id: RunId,
    actor: ActorDep,
    session: SessionDep,
    state: ApprovalStateFilter = None,
) -> ApprovalListResponse:
    principal = await execution_service.principal_for_run(session, actor, run_id)
    approvals = await service.list_run_approvals(session, principal, run_id=run_id, state=state)
    return ApprovalListResponse(
        items=[ApprovalResponse.model_validate(approval) for approval in approvals]
    )


@router.get(
    "/projects/{project_id}/approvals",
    response_model=ApprovalListResponse,
    summary="List approval requests across a project",
    responses=_ERRORS,
)
async def list_project_approvals(
    project_id: ProjectId,
    actor: ActorDep,
    session: SessionDep,
    state: ApprovalStateFilter = None,
) -> ApprovalListResponse:
    principal, _project = await identity_service.resolve_project(session, actor, project_id)
    approvals = await service.list_project_approvals(
        session, principal, project_id=project_id, state=state
    )
    return ApprovalListResponse(
        items=[ApprovalResponse.model_validate(approval) for approval in approvals]
    )


@router.get(
    "/approvals/{approval_id}",
    response_model=ApprovalResponse,
    summary="Fetch an approval request",
    responses=_ERRORS,
)
async def get_approval(
    approval_id: ApprovalId, actor: ActorDep, session: SessionDep
) -> ApprovalResponse:
    principal = await service.principal_for_approval(session, actor, approval_id)
    approval = await service.get_approval(session, principal, approval_id=approval_id)
    return ApprovalResponse.model_validate(approval)


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalResponse,
    summary="Approve, approve with edits, or reject a high-risk tool call",
    responses=_ERRORS,
)
async def decide_approval(
    approval_id: ApprovalId,
    body: DecideApprovalRequest,
    actor: ActorDep,
    session: SessionDep,
) -> ApprovalResponse:
    principal = await service.principal_for_approval(session, actor, approval_id)
    approval = await service.decide_approval(
        session, actor, principal, approval_id=approval_id, request=body
    )
    await session.commit()
    await session.refresh(approval)
    return ApprovalResponse.model_validate(approval)
