"""Evaluation-run use cases and outbox publishing."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import cast

import redis.asyncio as redis
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.auth.service import Actor, principal_for_organisation
from agentrail_api.execution.schemas import CreateEvaluationRunRequest
from agentrail_core.correlation import CorrelationContext
from agentrail_core.errors import (
    ForbiddenError,
    IdempotencyKeyReusedError,
    ValidationFailedError,
)
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
from agentrail_core.queue import publish_job

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


async def create_run(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    request: CreateEvaluationRunRequest,
    context: CorrelationContext,
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
        created_by=actor.user.id if actor.user else None,
    )
    session.add(run)
    partitions = _partitions_for_items(dataset_version.partition_counts, dataset_version.item_count)
    for index, partition in enumerate(partitions):
        session.add(
            RunItem(
                id=new_sortable_id(),
                run_id=run.id,
                item_index=index,
                partition=partition,
                state=RunItemState.PENDING,
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
