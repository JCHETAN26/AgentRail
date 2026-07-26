"""Agent definitions and immutable versions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status
from pydantic import BaseModel

from agentrail_api.agents import service
from agentrail_api.agents.schemas import (
    AgentDefinitionResponse,
    AgentVersionResponse,
    CreateAgentDefinitionRequest,
    CreateAgentVersionRequest,
)
from agentrail_api.dependencies import ActorDep, SessionDep
from agentrail_api.identity import service as identity_service
from agentrail_core.errors import ProblemDetail
from agentrail_core.identity import Permission, authorize

router = APIRouter(prefix="/api/v1", tags=["agents"])

ProjectId = Annotated[str, Path(min_length=26, max_length=26)]
AgentId = Annotated[str, Path(min_length=26, max_length=26)]
VersionId = Annotated[str, Path(min_length=26, max_length=26)]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ProblemDetail, "description": "Not signed in."},
    403: {"model": ProblemDetail, "description": "Not yours, or not permitted."},
    409: {"model": ProblemDetail, "description": "Already exists."},
    422: {"model": ProblemDetail, "description": "Validation failed."},
}


class AgentDefinitionListResponse(BaseModel):
    items: list[AgentDefinitionResponse]


class AgentVersionListResponse(BaseModel):
    items: list[AgentVersionResponse]


@router.post(
    "/projects/{project_id}/agents",
    response_model=AgentDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an agent definition",
    responses=_ERRORS,
)
async def create_agent_definition(
    project_id: ProjectId,
    body: CreateAgentDefinitionRequest,
    actor: ActorDep,
    session: SessionDep,
) -> AgentDefinitionResponse:
    principal, project = await identity_service.resolve_project(session, actor, project_id)
    authorize(principal, Permission.AGENT_MANAGE, organisation_id=project.organisation_id)
    agent = await service.create_agent_definition(
        session,
        actor,
        principal,
        project_id=project.id,
        name=body.name,
        description=body.description,
    )
    await session.commit()
    return AgentDefinitionResponse.model_validate(agent)


@router.get(
    "/projects/{project_id}/agents",
    response_model=AgentDefinitionListResponse,
    summary="List project agents",
    responses=_ERRORS,
)
async def list_agent_definitions(
    project_id: ProjectId, actor: ActorDep, session: SessionDep
) -> AgentDefinitionListResponse:
    principal, project = await identity_service.resolve_project(session, actor, project_id)
    agents = await service.list_agent_definitions(session, principal, project_id=project.id)
    return AgentDefinitionListResponse(
        items=[AgentDefinitionResponse.model_validate(agent) for agent in agents]
    )


@router.post(
    "/agents/{agent_id}/versions",
    response_model=AgentVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an immutable agent version",
    responses=_ERRORS,
)
async def create_agent_version(
    agent_id: AgentId,
    body: CreateAgentVersionRequest,
    actor: ActorDep,
    session: SessionDep,
) -> AgentVersionResponse:
    principal = await service.principal_for_agent(session, actor, agent_id)
    version = await service.create_agent_version(
        session, actor, principal, agent_id=agent_id, request=body
    )
    await session.commit()
    return AgentVersionResponse.model_validate(version)


@router.get(
    "/agents/{agent_id}/versions",
    response_model=AgentVersionListResponse,
    summary="List immutable versions for an agent",
    responses=_ERRORS,
)
async def list_agent_versions(
    agent_id: AgentId, actor: ActorDep, session: SessionDep
) -> AgentVersionListResponse:
    principal = await service.principal_for_agent(session, actor, agent_id)
    versions = await service.list_agent_versions(session, principal, agent_id=agent_id)
    return AgentVersionListResponse(
        items=[AgentVersionResponse.model_validate(version) for version in versions]
    )


@router.get(
    "/agent-versions/{version_id}",
    response_model=AgentVersionResponse,
    summary="Fetch an immutable agent version",
    responses=_ERRORS,
)
async def get_agent_version(
    version_id: VersionId, actor: ActorDep, session: SessionDep
) -> AgentVersionResponse:
    principal = await service.principal_for_agent_version(session, actor, version_id)
    version = await service.get_agent_version(session, principal, version_id=version_id)
    return AgentVersionResponse.model_validate(version)
