"""Agent registry use cases."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.agents.schemas import CreateAgentVersionRequest
from agentrail_api.auth.service import Actor, principal_for_organisation
from agentrail_api.identity.service import record_audit, slugify
from agentrail_core.agents import ToolContractError, canonical_tool_contracts
from agentrail_core.errors import ConflictError, ForbiddenError, ValidationFailedError
from agentrail_core.identity import (
    AgentDefinition,
    AgentVersion,
    Permission,
    Principal,
    Project,
    authorize,
)
from agentrail_core.ids import new_sortable_id


def version_content_digest(request: CreateAgentVersionRequest) -> str:
    """Return the canonical digest for an immutable version payload."""
    return _version_content_digest(
        request,
        tool_contracts=canonical_tool_contracts(request.tool_contracts),
    )


def _version_content_digest(
    request: CreateAgentVersionRequest, *, tool_contracts: list[dict[str, Any]]
) -> str:
    canonical = {
        "graph_spec": request.graph_spec,
        "prompt_bundle": request.prompt_bundle,
        "model_config": request.model_settings,
        "tool_contracts": tool_contracts,
        "policy_bundle": request.policy_bundle,
        "source_commit": request.source_commit,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


async def create_agent_definition(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    project_id: str,
    name: str,
    description: str | None,
) -> AgentDefinition:
    authorize(principal, Permission.AGENT_MANAGE, organisation_id=principal.organisation_id)

    slug = slugify(name)
    duplicate = await session.scalar(
        select(AgentDefinition.id).where(
            AgentDefinition.project_id == project_id, AgentDefinition.slug == slug
        )
    )
    if duplicate is not None:
        raise ConflictError("An agent with that name already exists.", details={"slug": slug})

    agent = AgentDefinition(
        id=new_sortable_id(),
        project_id=project_id,
        name=name.strip(),
        slug=slug,
        description=description.strip() if description else None,
        created_by=actor.user.id if actor.user else None,
    )
    session.add(agent)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "An agent with that name already exists.", details={"slug": slug}
        ) from exc

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="agent.created",
        target_type="agent",
        target_id=agent.id,
        context={"project_id": project_id, "slug": slug},
    )
    return agent


async def list_agent_definitions(
    session: AsyncSession, principal: Principal, *, project_id: str
) -> list[AgentDefinition]:
    authorize(principal, Permission.AGENT_READ, organisation_id=principal.organisation_id)
    rows = await session.scalars(
        select(AgentDefinition)
        .where(AgentDefinition.project_id == project_id)
        .order_by(AgentDefinition.id)
    )
    return list(rows.all())


async def principal_for_agent(session: AsyncSession, actor: Actor, agent_id: str) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(AgentDefinition, AgentDefinition.project_id == Project.id)
        .where(AgentDefinition.id == agent_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def principal_for_agent_version(
    session: AsyncSession, actor: Actor, version_id: str
) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(AgentDefinition, AgentDefinition.project_id == Project.id)
        .join(AgentVersion, AgentVersion.agent_id == AgentDefinition.id)
        .where(AgentVersion.id == version_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def get_agent_definition(
    session: AsyncSession,
    principal: Principal,
    *,
    agent_id: str,
    project_id: str | None = None,
    lock_for_update: bool = False,
) -> AgentDefinition:
    authorize(principal, Permission.AGENT_READ, organisation_id=principal.organisation_id)
    clauses: list[Any] = [
        AgentDefinition.id == agent_id,
        Project.organisation_id == principal.organisation_id,
    ]
    if project_id is not None:
        clauses.append(AgentDefinition.project_id == project_id)
    statement = (
        select(AgentDefinition)
        .join(Project, Project.id == AgentDefinition.project_id)
        .where(*clauses)
    )
    if lock_for_update:
        statement = statement.with_for_update(of=AgentDefinition)
    agent = await session.scalar(statement)
    if agent is None:
        raise ForbiddenError()
    return agent


async def create_agent_version(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    agent_id: str,
    request: CreateAgentVersionRequest,
) -> AgentVersion:
    authorize(principal, Permission.AGENT_MANAGE, organisation_id=principal.organisation_id)
    agent = await get_agent_definition(session, principal, agent_id=agent_id, lock_for_update=True)
    try:
        tool_contracts = canonical_tool_contracts(request.tool_contracts)
        digest = _version_content_digest(request, tool_contracts=tool_contracts)
    except ToolContractError as invalid:
        raise ValidationFailedError(
            "The agent version contains an invalid tool contract.",
            details={"reason": invalid.reason},
        ) from invalid

    duplicate_digest = await session.scalar(
        select(AgentVersion.id).where(
            AgentVersion.agent_id == agent.id, AgentVersion.content_digest == digest
        )
    )
    if duplicate_digest is not None:
        raise ConflictError(
            "That agent version content already exists.", details={"content_digest": digest}
        )

    current_version = await session.scalar(
        select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent.id)
    )
    version_number = int(current_version or 0) + 1
    version = AgentVersion(
        id=new_sortable_id(),
        agent_id=agent.id,
        version=version_number,
        content_digest=digest,
        graph_spec=request.graph_spec,
        prompt_bundle=request.prompt_bundle,
        model_config=request.model_settings,
        tool_contracts=tool_contracts,
        policy_bundle=request.policy_bundle,
        source_commit=request.source_commit,
        created_by=actor.user.id if actor.user else None,
    )
    session.add(version)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "That agent version already exists.", details={"agent_id": agent_id}
        ) from exc

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="agent_version.created",
        target_type="agent_version",
        target_id=version.id,
        context={"agent_id": agent.id, "version": version_number, "content_digest": digest},
    )
    return version


async def list_agent_versions(
    session: AsyncSession, principal: Principal, *, agent_id: str
) -> list[AgentVersion]:
    agent = await get_agent_definition(session, principal, agent_id=agent_id)
    rows = await session.scalars(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent.id)
        .order_by(AgentVersion.version.desc())
    )
    return list(rows.all())


async def get_agent_version(
    session: AsyncSession, principal: Principal, *, version_id: str
) -> AgentVersion:
    authorize(principal, Permission.AGENT_READ, organisation_id=principal.organisation_id)
    row = await session.execute(
        select(AgentVersion, Project)
        .join(AgentDefinition, AgentDefinition.id == AgentVersion.agent_id)
        .join(Project, Project.id == AgentDefinition.project_id)
        .where(AgentVersion.id == version_id)
    )
    result = row.one_or_none()
    if result is None:
        raise ForbiddenError()
    version = cast(AgentVersion, result[0])
    project = cast(Project, result[1])
    authorize(principal, Permission.AGENT_READ, organisation_id=project.organisation_id)
    return version
