"""Canary deployment use cases."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.auth.service import Actor, principal_for_organisation
from agentrail_api.deployments.schemas import CreateDeploymentRequest
from agentrail_api.identity.service import record_audit
from agentrail_core.deployments import Deployment, DeploymentState, evaluate_canary
from agentrail_core.errors import ConflictError, ForbiddenError
from agentrail_core.execution import EvaluationRun
from agentrail_core.identity import Permission, Principal, Project, authorize
from agentrail_core.ids import new_sortable_id
from agentrail_core.release import GateEvaluation, GateOutcome


async def principal_for_deployment(
    session: AsyncSession, actor: Actor, deployment_id: str
) -> Principal:
    organisation_id = await session.scalar(
        select(Project.organisation_id)
        .join(Deployment, Deployment.project_id == Project.id)
        .where(Deployment.id == deployment_id)
    )
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def principal_for_run(session: AsyncSession, actor: Actor, run_id: str) -> Principal:
    organisation_id = await session.scalar(
        select(Project.organisation_id)
        .join(EvaluationRun, EvaluationRun.project_id == Project.id)
        .where(EvaluationRun.id == run_id)
    )
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def list_deployments(
    session: AsyncSession, principal: Principal, *, project_id: str
) -> list[Deployment]:
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    rows = await session.scalars(
        select(Deployment)
        .join(Project, Project.id == Deployment.project_id)
        .where(
            Deployment.project_id == project_id,
            Project.organisation_id == principal.organisation_id,
        )
        .order_by(Deployment.created_at.desc(), Deployment.id.desc())
    )
    return list(rows.all())


async def create_deployment(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    request: CreateDeploymentRequest,
) -> Deployment:
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)
    run = await _run_for_deployment(session, principal=principal, run_id=request.run_id)
    gate = await _passing_gate(
        session,
        run_id=run.id,
        gate_evaluation_id=request.gate_evaluation_id,
    )
    decision = evaluate_canary(
        baseline=request.baseline_metrics,
        observed=request.canary_metrics,
        thresholds=request.thresholds,
    )
    now = datetime.now(UTC)
    state = DeploymentState.PROMOTED if decision.promotes else DeploymentState.ROLLED_BACK
    deployment = Deployment(
        id=new_sortable_id(),
        project_id=run.project_id,
        run_id=run.id,
        gate_evaluation_id=gate.id,
        candidate_agent_version_id=run.candidate_agent_version_id,
        environment=request.environment,
        state=state,
        traffic_percent=100 if decision.promotes else 0,
        workload=request.workload,
        baseline_metrics=request.baseline_metrics,
        canary_metrics=request.canary_metrics,
        thresholds=request.thresholds,
        deltas=decision.deltas,
        decision=decision.as_payload(),
        rollback_reason="; ".join(decision.reasons)[:1024] if decision.reasons else None,
        created_by=actor.user.id if actor.user else None,
        promoted_at=now if decision.promotes else None,
        rolled_back_at=now if not decision.promotes else None,
    )
    session.add(deployment)
    await _audit(session, actor, principal, deployment, "deployment.created")
    await session.flush()
    return deployment


async def promote_deployment(
    session: AsyncSession, actor: Actor, principal: Principal, *, deployment_id: str
) -> Deployment:
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)
    deployment = await _deployment_for_write(session, principal, deployment_id=deployment_id)
    if deployment.state == DeploymentState.ROLLED_BACK:
        raise ConflictError("A rolled-back deployment cannot be promoted.")
    if deployment.state != DeploymentState.PROMOTED:
        deployment.state = DeploymentState.PROMOTED
        deployment.traffic_percent = 100
        deployment.promoted_at = datetime.now(UTC)
        await _audit(session, actor, principal, deployment, "deployment.promoted")
    await session.flush()
    return deployment


async def rollback_deployment(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    deployment_id: str,
    reason: str,
) -> Deployment:
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)
    deployment = await _deployment_for_write(session, principal, deployment_id=deployment_id)
    if deployment.state != DeploymentState.ROLLED_BACK:
        deployment.state = DeploymentState.ROLLED_BACK
        deployment.traffic_percent = 0
        deployment.rolled_back_at = datetime.now(UTC)
    deployment.rollback_reason = reason
    await _audit(session, actor, principal, deployment, "deployment.rolled_back")
    await session.flush()
    return deployment


async def _run_for_deployment(
    session: AsyncSession, *, principal: Principal, run_id: str
) -> EvaluationRun:
    run = await session.scalar(
        select(EvaluationRun)
        .join(Project, Project.id == EvaluationRun.project_id)
        .where(
            EvaluationRun.id == run_id,
            Project.organisation_id == principal.organisation_id,
        )
    )
    if run is None:
        raise ForbiddenError()
    return run


async def _passing_gate(
    session: AsyncSession, *, run_id: str, gate_evaluation_id: str | None
) -> GateEvaluation:
    query = select(GateEvaluation).where(
        GateEvaluation.run_id == run_id,
        GateEvaluation.outcome == GateOutcome.PASSED.value,
    )
    if gate_evaluation_id is not None:
        query = query.where(GateEvaluation.id == gate_evaluation_id)
    else:
        query = query.order_by(GateEvaluation.created_at.desc(), GateEvaluation.id.desc())
    gate = await session.scalar(query)
    if gate is None:
        raise ConflictError("A deployment requires a passing release gate for this run.")
    return gate


async def _deployment_for_write(
    session: AsyncSession, principal: Principal, *, deployment_id: str
) -> Deployment:
    deployment = await session.scalar(
        select(Deployment)
        .join(Project, Project.id == Deployment.project_id)
        .where(
            Deployment.id == deployment_id,
            Project.organisation_id == principal.organisation_id,
        )
    )
    if deployment is None:
        raise ForbiddenError()
    return deployment


async def _audit(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    deployment: Deployment,
    action: str,
) -> None:
    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action=action,
        target_type="deployment",
        target_id=deployment.id,
        context={
            "project_id": deployment.project_id,
            "run_id": deployment.run_id,
            "state": deployment.state,
            "traffic_percent": deployment.traffic_percent,
        },
    )
