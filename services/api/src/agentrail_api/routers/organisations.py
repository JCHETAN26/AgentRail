"""Organisations, members, API keys and the audit log.

Every handler resolves a :class:`Principal` for the organisation in the path
*before* doing anything else. That single call is both the tenancy check and the
permission check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query, status
from pydantic import BaseModel

from agentrail_api.auth.service import principal_for_organisation
from agentrail_api.dependencies import ActorDep, SessionDep, SettingsDep
from agentrail_api.identity import service
from agentrail_api.identity.schemas import (
    AddMemberRequest,
    ApiKeyResponse,
    AuditEventResponse,
    AuditRetentionResponse,
    CreateApiKeyRequest,
    CreatedApiKeyResponse,
    CreateOrganisationRequest,
    CreateProjectRequest,
    MemberResponse,
    OrganisationResponse,
    ProjectResponse,
    UserResponse,
)
from agentrail_core.errors import ProblemDetail
from agentrail_core.identity import Permission, authorize

router = APIRouter(prefix="/api/v1/organisations", tags=["organisations"])

OrganisationId = Annotated[str, Path(min_length=26, max_length=26)]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
    409: {"model": ProblemDetail, "description": "Already exists."},
    422: {"model": ProblemDetail, "description": "Validation failed."},
}


class OrganisationListResponse(BaseModel):
    items: list[OrganisationResponse]


class MemberListResponse(BaseModel):
    items: list[MemberResponse]


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]


class ApiKeyListResponse(BaseModel):
    items: list[ApiKeyResponse]


class AuditEventListResponse(BaseModel):
    items: list[AuditEventResponse]


@router.post(
    "",
    response_model=OrganisationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organisation",
    responses=_ERRORS,
)
async def create_organisation(
    body: CreateOrganisationRequest, actor: ActorDep, session: SessionDep
) -> OrganisationResponse:
    organisation, _project = await service.create_organisation(session, actor, name=body.name)
    await session.commit()
    return OrganisationResponse.model_validate(organisation)


@router.get(
    "",
    response_model=OrganisationListResponse,
    summary="List organisations the caller belongs to",
    responses=_ERRORS,
)
async def list_organisations(actor: ActorDep, session: SessionDep) -> OrganisationListResponse:
    memberships = await service.list_organisations_for_actor(session, actor)
    return OrganisationListResponse(
        items=[OrganisationResponse.model_validate(item.organisation) for item in memberships]
    )


@router.get(
    "/{organisation_id}",
    response_model=OrganisationResponse,
    summary="Fetch one organisation",
    responses=_ERRORS,
)
async def get_organisation(
    organisation_id: OrganisationId, actor: ActorDep, session: SessionDep
) -> OrganisationResponse:
    principal = await principal_for_organisation(session, actor, organisation_id)
    authorize(principal, Permission.ORGANISATION_READ, organisation_id=organisation_id)
    organisation = await service.get_organisation(session, principal)
    return OrganisationResponse.model_validate(organisation)


@router.get(
    "/{organisation_id}/members",
    response_model=MemberListResponse,
    summary="List members",
    responses=_ERRORS,
)
async def list_members(
    organisation_id: OrganisationId, actor: ActorDep, session: SessionDep
) -> MemberListResponse:
    principal = await principal_for_organisation(session, actor, organisation_id)
    rows = await service.list_members(session, principal)
    return MemberListResponse(
        items=[
            MemberResponse(
                user=UserResponse.model_validate(user),
                role=membership.role,
                created_at=membership.created_at,
            )
            for user, membership in rows
        ]
    )


@router.post(
    "/{organisation_id}/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a role to an existing user",
    responses=_ERRORS,
)
async def add_member(
    organisation_id: OrganisationId,
    body: AddMemberRequest,
    actor: ActorDep,
    session: SessionDep,
) -> MemberResponse:
    principal = await principal_for_organisation(session, actor, organisation_id)
    membership = await service.add_member(
        session, actor, principal, email=str(body.email), role=body.role
    )
    await session.commit()
    user = await service.get_user(session, membership.user_id)
    return MemberResponse(
        user=UserResponse.model_validate(user),
        role=membership.role,
        created_at=membership.created_at,
    )


@router.post(
    "/{organisation_id}/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    responses=_ERRORS,
)
async def create_project(
    organisation_id: OrganisationId,
    body: CreateProjectRequest,
    actor: ActorDep,
    session: SessionDep,
) -> ProjectResponse:
    principal = await principal_for_organisation(session, actor, organisation_id)
    project = await service.create_project(session, actor, principal, name=body.name)
    await session.commit()
    return ProjectResponse.model_validate(project)


@router.get(
    "/{organisation_id}/projects",
    response_model=ProjectListResponse,
    summary="List projects",
    responses=_ERRORS,
)
async def list_projects(
    organisation_id: OrganisationId, actor: ActorDep, session: SessionDep
) -> ProjectListResponse:
    principal = await principal_for_organisation(session, actor, organisation_id)
    projects = await service.list_projects(session, principal)
    return ProjectListResponse(
        items=[ProjectResponse.model_validate(project) for project in projects]
    )


@router.post(
    "/{organisation_id}/api-keys",
    response_model=CreatedApiKeyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a scoped API key",
    responses=_ERRORS,
)
async def create_api_key(
    organisation_id: OrganisationId,
    body: CreateApiKeyRequest,
    actor: ActorDep,
    session: SessionDep,
) -> CreatedApiKeyResponse:
    """The response contains the only copy of the token that will ever exist."""
    principal = await principal_for_organisation(session, actor, organisation_id)
    issued = await service.create_api_key(
        session,
        actor,
        principal,
        name=body.name,
        role=body.role,
        scopes=body.scopes,
        expires_at=body.expires_at,
    )
    await session.commit()
    return CreatedApiKeyResponse(
        key=ApiKeyResponse.model_validate(issued.record), token=issued.token
    )


@router.get(
    "/{organisation_id}/api-keys",
    response_model=ApiKeyListResponse,
    summary="List API keys (never includes secrets)",
    responses=_ERRORS,
)
async def list_api_keys(
    organisation_id: OrganisationId, actor: ActorDep, session: SessionDep
) -> ApiKeyListResponse:
    principal = await principal_for_organisation(session, actor, organisation_id)
    keys = await service.list_api_keys(session, principal)
    return ApiKeyListResponse(items=[ApiKeyResponse.model_validate(key) for key in keys])


@router.delete(
    "/{organisation_id}/api-keys/{key_id}",
    response_model=ApiKeyResponse,
    summary="Revoke an API key",
    responses=_ERRORS,
)
async def revoke_api_key(
    organisation_id: OrganisationId,
    key_id: Annotated[str, Path(min_length=26, max_length=26)],
    actor: ActorDep,
    session: SessionDep,
) -> ApiKeyResponse:
    principal = await principal_for_organisation(session, actor, organisation_id)
    record = await service.revoke_api_key(session, actor, principal, key_id=key_id)
    await session.commit()
    return ApiKeyResponse.model_validate(record)


@router.post(
    "/{organisation_id}/api-keys/{key_id}/rotate",
    response_model=CreatedApiKeyResponse,
    summary="Rotate an API key secret",
    responses=_ERRORS,
)
async def rotate_api_key(
    organisation_id: OrganisationId,
    key_id: Annotated[str, Path(min_length=26, max_length=26)],
    actor: ActorDep,
    session: SessionDep,
) -> CreatedApiKeyResponse:
    """Return the only copy of the replacement token."""
    principal = await principal_for_organisation(session, actor, organisation_id)
    issued = await service.rotate_api_key(session, actor, principal, key_id=key_id)
    await session.commit()
    return CreatedApiKeyResponse(
        key=ApiKeyResponse.model_validate(issued.record), token=issued.token
    )


@router.get(
    "/{organisation_id}/audit-events",
    response_model=AuditEventListResponse,
    summary="Recent audit events",
    responses=_ERRORS,
)
async def list_audit_events(
    organisation_id: OrganisationId,
    actor: ActorDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = 50,
) -> AuditEventListResponse:
    principal = await principal_for_organisation(session, actor, organisation_id)
    events = await service.list_audit_events(session, principal, limit=limit)
    return AuditEventListResponse(
        items=[AuditEventResponse.model_validate(event) for event in events]
    )


@router.delete(
    "/{organisation_id}/audit-events/expired",
    response_model=AuditRetentionResponse,
    summary="Prune expired audit events",
    responses=_ERRORS,
)
async def prune_expired_audit_events(
    organisation_id: OrganisationId,
    actor: ActorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> AuditRetentionResponse:
    principal = await principal_for_organisation(session, actor, organisation_id)
    cutoff, deleted_count = await service.prune_expired_audit_events(
        session, actor, principal, settings, now=datetime.now(UTC)
    )
    await session.commit()
    return AuditRetentionResponse(
        retention_days=settings.audit_event_retention_days,
        cutoff=cutoff,
        deleted_count=deleted_count,
    )
