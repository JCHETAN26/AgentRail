"""Evaluation-run use cases and outbox publishing."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any, cast

import redis.asyncio as redis
from sqlalchemy import Table, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.auth.service import Actor, principal_for_organisation
from agentrail_api.execution.schemas import (
    CreateEvaluationRunRequest,
    EvaluationRunMetricsResponse,
    RunItemRecoveryResponse,
    RunRecoveryResponse,
)
from agentrail_api.release.service import assert_repository_claim
from agentrail_api.settings import ApiSettings
from agentrail_core.approvals import ApprovalRequest, ApprovalState
from agentrail_core.correlation import CorrelationContext
from agentrail_core.deployments import Deployment, DeploymentState
from agentrail_core.errors import (
    ForbiddenError,
    IdempotencyKeyReusedError,
    QuotaExceededError,
    ValidationFailedError,
)
from agentrail_core.evaluators import ComparisonReport
from agentrail_core.execution import (
    TERMINAL_ITEM_STATES,
    TERMINAL_RUN_STATES,
    EvaluationRun,
    EvaluationRunState,
    OutboxEvent,
    RunItem,
    RunItemState,
)
from agentrail_core.identity import (
    AgentDefinition,
    AgentVersion,
    Dataset,
    DatasetVersion,
    EvaluationSuite,
    Permission,
    Principal,
    Project,
    authorize,
)
from agentrail_core.ids import new_sortable_id
from agentrail_core.observability import evaluate_run_slo
from agentrail_core.queue import publish_job
from agentrail_core.quotas import OrganisationQuotaPeriod
from agentrail_core.release import GateEvaluation, GateOutcome
from agentrail_core.side_effects import SideEffectRecord
from agentrail_core.trajectories import Trajectory

MAX_RUN_ITEMS = 1_000
OUTBOX_EVENT_RUN_CREATED = "evaluation_run.created"


def run_request_fingerprint(request: CreateEvaluationRunRequest) -> str:
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def principal_for_run(session: AsyncSession, actor: Actor, run_id: str) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(EvaluationRun, EvaluationRun.project_id == Project.id)
        .where(EvaluationRun.id == run_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def principal_for_suite(session: AsyncSession, actor: Actor, suite_id: str) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(EvaluationSuite, EvaluationSuite.project_id == Project.id)
        .where(EvaluationSuite.id == suite_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def get_run(session: AsyncSession, principal: Principal, *, run_id: str) -> EvaluationRun:
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    run = await session.scalar(
        select(EvaluationRun)
        .join(Project, Project.id == EvaluationRun.project_id)
        .where(EvaluationRun.id == run_id, Project.organisation_id == principal.organisation_id)
    )
    if run is None:
        raise ForbiddenError()
    return run


async def progress_snapshot(
    session: AsyncSession, principal: Principal, *, run_id: str
) -> tuple[EvaluationRun, dict[RunItemState, int]]:
    run = await get_run(session, principal, run_id=run_id)
    rows = await session.execute(
        select(RunItem.state, func.count()).where(RunItem.run_id == run.id).group_by(RunItem.state)
    )
    counts = {RunItemState(state): int(count) for state, count in rows.all()}
    return run, counts


async def run_metrics(
    session: AsyncSession, principal: Principal, *, run_id: str
) -> EvaluationRunMetricsResponse:
    run = await get_run(session, principal, run_id=run_id)
    items = list(
        (
            await session.scalars(
                select(RunItem).where(RunItem.run_id == run.id).order_by(RunItem.item_index)
            )
        ).all()
    )
    comparison = await session.scalar(
        select(ComparisonReport).where(ComparisonReport.run_id == run.id)
    )
    gates = list(
        (
            await session.scalars(
                select(GateEvaluation)
                .where(GateEvaluation.run_id == run.id)
                .order_by(GateEvaluation.created_at)
            )
        ).all()
    )
    deployments = list(
        (
            await session.scalars(
                select(Deployment)
                .where(Deployment.run_id == run.id)
                .order_by(Deployment.created_at)
            )
        ).all()
    )
    outbox_events = list(
        (
            await session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_type == "evaluation_run",
                    OutboxEvent.aggregate_id == run.id,
                )
                .order_by(OutboxEvent.created_at)
            )
        ).all()
    )
    trajectory_count = int(
        await session.scalar(
            select(func.count()).select_from(Trajectory).where(Trajectory.run_id == run.id)
        )
        or 0
    )
    approval_counts = {
        str(state): int(count)
        for state, count in (
            await session.execute(
                select(ApprovalRequest.state, func.count())
                .where(ApprovalRequest.run_id == run.id)
                .group_by(ApprovalRequest.state)
            )
        ).all()
    }
    side_effect_count = int(
        await session.scalar(
            select(func.count())
            .select_from(SideEffectRecord)
            .where(SideEffectRecord.run_id == run.id)
        )
        or 0
    )

    item_states: dict[str, int] = {}
    retry_count = 0
    stranded_count = 0
    now = datetime.now(UTC)
    budgets = _budget_totals(items)
    for item in items:
        item_states[str(item.state)] = item_states.get(str(item.state), 0) + 1
        if item.attempt_count > 1:
            retry_count += 1
        if (
            item.lease_expires_at is not None
            and item.lease_expires_at < now
            and item.state not in TERMINAL_ITEM_STATES
        ):
            stranded_count += 1

    quality = _quality_metrics(comparison=comparison, run=run)
    release = _release_metrics(gates)
    canary = _canary_metrics(deployments)
    metrics_payload = {
        "run": {"failed_count": run.failed_count},
        "quality": quality,
        "reliability": {"stranded_count": stranded_count},
        "budgets": budgets,
        "canary": canary,
    }
    slo = evaluate_run_slo(metrics_payload).as_payload()

    return EvaluationRunMetricsResponse.model_validate(
        {
            "run_id": run.id,
            "project_id": run.project_id,
            "correlation": {
                "correlation_id": run.correlation_id,
                "trace_id": run.trace_id,
                "traceparent": f"00-{run.trace_id}-0000000000000001-01",
            },
            "trace_links": {
                "run": f"/api/v1/evaluation-runs/{run.id}",
                "events": f"/api/v1/evaluation-runs/{run.id}/events",
                "recovery": f"/api/v1/evaluation-runs/{run.id}/recovery",
                "items": f"/api/v1/evaluation-runs/{run.id}/items",
                "trajectories": trajectory_count,
            },
            "run": {
                "state": str(run.state),
                "item_count": run.item_count,
                "completed_count": run.completed_count,
                "failed_count": run.failed_count,
                "created_at": run.created_at,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
            },
            "queue": {
                "item_states": item_states,
                "pending_count": item_states.get(str(RunItemState.PENDING), 0),
                "leased_count": item_states.get(str(RunItemState.LEASED), 0),
                "outbox_published": bool(outbox_events)
                and all(event.published_at is not None for event in outbox_events),
                "outbox_attempts": sum(event.attempts for event in outbox_events),
                "outbox_event_count": len(outbox_events),
                "outbox_pending_count": sum(
                    1 for event in outbox_events if event.published_at is None
                ),
                "outbox_published_count": sum(
                    1 for event in outbox_events if event.published_at is not None
                ),
            },
            "reliability": {
                "retried_count": retry_count,
                "stranded_count": stranded_count,
                "side_effect_count": side_effect_count,
            },
            "budgets": budgets,
            "quality": quality,
            "policy": {
                "approval_counts": approval_counts,
                "awaiting_approval_count": approval_counts.get(str(ApprovalState.PENDING), 0),
            },
            "release": release,
            "canary": canary,
            "slo": slo,
            "runbook": {
                "title": "Evaluation run incident",
                "path": "docs/operations/INCIDENT_RUNBOOK.md",
                "first_steps": [
                    "Quote the correlation_id in every handoff.",
                    "Open the recovery link to identify stranded leases or duplicate effects.",
                    "Inspect quality, release and canary sections before deciding rollback.",
                ],
            },
        }
    )


def _budget_totals(items: list[RunItem]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {"limits": {}, "spent": {}, "remaining": {}}
    for item in items:
        for section in totals:
            raw = item.budget_state.get(section) if isinstance(item.budget_state, dict) else None
            if not isinstance(raw, dict):
                continue
            for key, value in raw.items():
                if isinstance(value, bool) or not isinstance(value, int | float):
                    continue
                bucket = totals[section]
                bucket[str(key)] = bucket.get(str(key), 0) + int(value)
    return totals


def _quality_metrics(*, comparison: ComparisonReport | None, run: EvaluationRun) -> dict[str, Any]:
    if comparison is None:
        completed = max(run.completed_count, 0)
        return {
            "has_report": False,
            "pass_rate": 0.0,
            "regression_count": 0,
            "completed_items": completed,
            "failed_items": run.failed_count,
        }
    return {
        "has_report": True,
        "pass_rate": _number(comparison.summary.get("pass_rate")),
        "regression_count": int(comparison.summary.get("regression_count", 0) or 0),
        "completed_items": run.completed_count,
        "failed_items": run.failed_count,
        "evaluator_metrics": comparison.evaluator_metrics,
        "category_metrics": comparison.category_metrics,
    }


def _release_metrics(gates: list[GateEvaluation]) -> dict[str, Any]:
    blocked = sum(1 for gate in gates if gate.outcome == GateOutcome.BLOCKED.value)
    passed = sum(1 for gate in gates if gate.outcome == GateOutcome.PASSED.value)
    return {
        "gate_count": len(gates),
        "passed_count": passed,
        "blocked_count": blocked,
        "latest": gates[-1].summary if gates else None,
    }


def _canary_metrics(deployments: list[Deployment]) -> dict[str, Any]:
    promoted = sum(1 for deployment in deployments if deployment.state == DeploymentState.PROMOTED)
    rolled_back = sum(
        1 for deployment in deployments if deployment.state == DeploymentState.ROLLED_BACK
    )
    return {
        "deployment_count": len(deployments),
        "promoted_count": promoted,
        "rollback_count": rolled_back,
        "latest_state": str(deployments[-1].state) if deployments else None,
        "latest_deltas": deployments[-1].deltas if deployments else {},
        "latest_rollback_reason": deployments[-1].rollback_reason if deployments else None,
    }


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


async def _suite_scope(
    session: AsyncSession, principal: Principal, suite_id: str
) -> tuple[EvaluationSuite, DatasetVersion, Dataset, Project]:
    row = await session.execute(
        select(EvaluationSuite, DatasetVersion, Dataset, Project)
        .join(Project, Project.id == EvaluationSuite.project_id)
        .join(DatasetVersion, DatasetVersion.id == EvaluationSuite.dataset_version_id)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .where(EvaluationSuite.id == suite_id)
    )
    result = row.one_or_none()
    if result is None:
        raise ForbiddenError()
    suite, version, dataset, project = result
    authorize(principal, Permission.RUN_CREATE, organisation_id=project.organisation_id)
    if suite.frozen_at is None:
        raise ValidationFailedError("Evaluation suite must be frozen before it can run.")
    if dataset.project_id != suite.project_id:
        raise ForbiddenError()
    return suite, version, dataset, project


async def _agent_version_in_project(
    session: AsyncSession, principal: Principal, *, version_id: str, project_id: str
) -> AgentVersion:
    row = await session.execute(
        select(AgentVersion, Project)
        .join(AgentDefinition, AgentDefinition.id == AgentVersion.agent_id)
        .join(Project, Project.id == AgentDefinition.project_id)
        .where(AgentVersion.id == version_id, AgentDefinition.project_id == project_id)
    )
    result = row.one_or_none()
    if result is None:
        raise ForbiddenError()
    version = cast(AgentVersion, result[0])
    project = cast(Project, result[1])
    authorize(principal, Permission.RUN_CREATE, organisation_id=project.organisation_id)
    return version


def _partitions_for_items(partition_counts: dict[str, int], item_count: int) -> list[str]:
    partitions: list[str] = []
    for partition, count in sorted(partition_counts.items()):
        partitions.extend([partition] * int(count))
    if len(partitions) < item_count:
        partitions.extend(["default"] * (item_count - len(partitions)))
    return partitions[:item_count]


async def _find_by_idempotency_key(
    session: AsyncSession, key: str, *, project_id: str
) -> EvaluationRun | None:
    run: EvaluationRun | None = await session.scalar(
        select(EvaluationRun).where(
            EvaluationRun.idempotency_key == key, EvaluationRun.project_id == project_id
        )
    )
    return run


def _quota_period_start(now: datetime) -> date:
    return date(now.year, now.month, 1)


async def _charge_evaluation_item_quota(
    session: AsyncSession,
    settings: ApiSettings,
    *,
    organisation_id: str,
    item_count: int,
    now: datetime,
) -> None:
    limit = settings.evaluation_item_monthly_quota
    period_start = _quota_period_start(now)
    if item_count > limit:
        raise QuotaExceededError(
            "This evaluation suite exceeds the organisation's monthly item quota.",
            details={
                "limit": limit,
                "requested": item_count,
                "period_start": period_start.isoformat(),
            },
        )

    table = cast(Table, OrganisationQuotaPeriod.__table__)
    statement = (
        insert(table)
        .values(
            id=new_sortable_id(),
            organisation_id=organisation_id,
            period_start=period_start,
            evaluation_item_limit=limit,
            evaluation_items_used=item_count,
        )
        .on_conflict_do_update(
            index_elements=[table.c.organisation_id, table.c.period_start],
            set_={
                "evaluation_item_limit": limit,
                "evaluation_items_used": table.c.evaluation_items_used + item_count,
                "updated_at": func.now(),
            },
            where=(table.c.evaluation_items_used + item_count) <= limit,
        )
        .returning(table.c.evaluation_items_used)
    )
    used_after = (await session.execute(statement)).scalar_one_or_none()
    if used_after is not None:
        return

    used = await session.scalar(
        select(OrganisationQuotaPeriod.evaluation_items_used).where(
            OrganisationQuotaPeriod.organisation_id == organisation_id,
            OrganisationQuotaPeriod.period_start == period_start,
        )
    )
    raise QuotaExceededError(
        "This evaluation run would exceed the organisation's monthly item quota.",
        details={
            "limit": limit,
            "used": int(used or 0),
            "requested": item_count,
            "period_start": period_start.isoformat(),
        },
    )


async def create_run(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    request: CreateEvaluationRunRequest,
    context: CorrelationContext,
    settings: ApiSettings,
    *,
    idempotency_key: str | None = None,
) -> tuple[EvaluationRun, bool, OutboxEvent | None]:
    suite, dataset_version, _dataset, project = await _suite_scope(
        session, principal, request.evaluation_suite_id
    )
    project_id = project.id
    await _agent_version_in_project(
        session,
        principal,
        version_id=request.candidate_agent_version_id,
        project_id=project_id,
    )
    if request.baseline_agent_version_id is not None:
        await _agent_version_in_project(
            session,
            principal,
            version_id=request.baseline_agent_version_id,
            project_id=project_id,
        )

    fingerprint = run_request_fingerprint(request)
    if idempotency_key is not None:
        existing = await _find_by_idempotency_key(session, idempotency_key, project_id=project_id)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise IdempotencyKeyReusedError(
                    "This idempotency key was already used with a different request body.",
                    details={"idempotency_key": idempotency_key, "run_id": existing.id},
                )
            return existing, False, None

    if dataset_version.item_count <= 0 or dataset_version.item_count > MAX_RUN_ITEMS:
        raise ValidationFailedError(
            "Dataset version item count is outside the executable range.",
            details={"item_count": dataset_version.item_count, "max_items": MAX_RUN_ITEMS},
        )

    await _charge_evaluation_item_quota(
        session,
        settings,
        organisation_id=project.organisation_id,
        item_count=dataset_version.item_count,
        now=datetime.now(UTC),
    )

    # A project may only assert provenance for a repository it has bound.
    # Without this, provenance is client-supplied and one tenant could name
    # another's repository, so that tenant's own webhook would cancel its runs.
    await assert_repository_claim(
        session,
        project_id=project_id,
        owner=request.github_owner,
        repository=request.github_repository,
    )

    run = EvaluationRun(
        id=new_sortable_id(),
        project_id=project_id,
        evaluation_suite_id=suite.id,
        candidate_agent_version_id=request.candidate_agent_version_id,
        baseline_agent_version_id=request.baseline_agent_version_id,
        execution_mode=request.execution_mode,
        state=EvaluationRunState.CREATED,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        correlation_id=context.correlation_id,
        trace_id=context.trace_id,
        item_count=dataset_version.item_count,
        summary={
            "dataset_version_id": dataset_version.id,
            "storage_uri": dataset_version.storage_uri,
            "partition_counts": dataset_version.partition_counts,
        },
        # Carried through from the request so the release gate can publish a
        # Check Run against the right commit, and so a later push to the same
        # pull request can supersede this run.
        github_owner=request.github_owner,
        github_repository=request.github_repository,
        github_pull_number=request.github_pull_number,
        github_head_sha=request.github_head_sha,
        created_by=actor.user.id if actor.user else None,
    )
    session.add(run)
    partitions = _partitions_for_items(dataset_version.partition_counts, dataset_version.item_count)
    # The record travels with the item. Reaching back through the suite to the
    # dataset at execution time would couple the worker to data that may have
    # been superseded, and a frozen suite's whole point is that what ran is what
    # was frozen.
    records = dataset_version.records or []
    for index, partition in enumerate(partitions):
        session.add(
            RunItem(
                id=new_sortable_id(),
                run_id=run.id,
                item_index=index,
                partition=partition,
                state=RunItemState.PENDING,
                payload=dict(records[index]) if index < len(records) else {},
                checkpoint={"dataset_version_id": dataset_version.id, "item_index": index},
            )
        )
    event = OutboxEvent(
        id=new_sortable_id(),
        event_type=OUTBOX_EVENT_RUN_CREATED,
        aggregate_type="evaluation_run",
        aggregate_id=run.id,
        payload={"run_id": run.id},
    )
    session.add(event)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if idempotency_key is None:
            raise
        existing = await _find_by_idempotency_key(session, idempotency_key, project_id=project_id)
        if existing is None:  # pragma: no cover
            raise
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyKeyReusedError(
                "This idempotency key was already used with a different request body.",
                details={"idempotency_key": idempotency_key, "run_id": existing.id},
            ) from exc
        return existing, False, None
    return run, True, event


async def publish_outbox_event(
    session: AsyncSession, redis_client: redis.Redis, *, event_id: str, run_queue_key: str
) -> bool:
    event = await session.get(OutboxEvent, event_id)
    if event is None or event.published_at is not None:
        return False
    await publish_job(redis_client, run_queue_key, str(event.payload["run_id"]))
    event.published_at = datetime.now(UTC)
    event.attempts += 1
    await session.commit()
    return True


async def cancel_run(
    session: AsyncSession, actor: Actor, principal: Principal, *, run_id: str
) -> EvaluationRun:
    authorize(principal, Permission.RUN_CANCEL, organisation_id=principal.organisation_id)
    run = await session.scalar(
        select(EvaluationRun)
        .join(Project, Project.id == EvaluationRun.project_id)
        .where(EvaluationRun.id == run_id, Project.organisation_id == principal.organisation_id)
        .with_for_update()
    )
    if run is None:
        raise ForbiddenError()
    if run.state in TERMINAL_RUN_STATES:
        return run
    run.state = EvaluationRunState.CANCELLED
    run.cancelled_at = datetime.now(UTC)
    run.completed_at = run.cancelled_at
    run.updated_at = run.cancelled_at
    run.version += 1
    await session.execute(
        update(RunItem)
        .where(
            RunItem.run_id == run.id,
            RunItem.state.not_in(tuple(state.value for state in TERMINAL_ITEM_STATES)),
        )
        .values(state=RunItemState.CANCELLED, completed_at=func.now(), updated_at=func.now())
    )
    await session.flush()
    return run


async def run_recovery(
    session: AsyncSession, principal: Principal, *, run_id: str
) -> RunRecoveryResponse:
    """The reliability view of a run: attempts, leases, faults and effects.

    Answers the question an operator actually asks during an incident — what is
    stuck, what has been retried, and did anything act on the world twice —
    without making them join four tables by hand.
    """
    run = await get_run(session, principal, run_id=run_id)
    items = list(
        (
            await session.scalars(
                select(RunItem).where(RunItem.run_id == run.id).order_by(RunItem.item_index)
            )
        ).all()
    )
    effects_by_item = {
        str(item_id): int(count)
        for item_id, count in (
            await session.execute(
                select(SideEffectRecord.run_item_id, func.count())
                .where(SideEffectRecord.run_id == run.id)
                .group_by(SideEffectRecord.run_item_id)
            )
        ).all()
    }

    now = datetime.now(UTC)
    item_states: dict[RunItemState, int] = {}
    stranded = 0
    retried = 0
    rows: list[RunItemRecoveryResponse] = []
    for item in items:
        item_states[item.state] = item_states.get(item.state, 0) + 1
        expired = item.lease_expires_at is not None and item.lease_expires_at < now
        if expired and item.state not in TERMINAL_ITEM_STATES:
            stranded += 1
        if item.attempt_count > 1:
            retried += 1
        rows.append(
            RunItemRecoveryResponse(
                id=item.id,
                item_index=item.item_index,
                partition=item.partition,
                state=item.state,
                attempt_count=item.attempt_count,
                max_attempts=item.max_attempts,
                retries_remaining=max(item.max_attempts - item.attempt_count, 0),
                worker_id=item.worker_id,
                lease_expires_at=item.lease_expires_at,
                lease_expired=expired,
                injected_fault=item.injected_fault,
                budget_state=item.budget_state,
                side_effect_count=effects_by_item.get(item.id, 0),
                error_code=item.error_code,
                error_message=item.error_message,
            )
        )

    return RunRecoveryResponse(
        run_id=run.id,
        item_states=item_states,
        stranded_count=stranded,
        retried_count=retried,
        side_effect_count=sum(effects_by_item.values()),
        items=rows,
    )
