"""Approval use cases."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.approvals.schemas import DecideApprovalRequest
from agentrail_api.auth.service import Actor, principal_for_organisation
from agentrail_api.identity.service import record_audit
from agentrail_core.approvals import (
    ApprovalRequest,
    ApprovalState,
    assert_approval_transition,
    is_terminal_approval,
)
from agentrail_core.errors import ConflictError, ForbiddenError
from agentrail_core.execution import EvaluationRun, OutboxEvent, RunItem, RunItemState
from agentrail_core.identity import Permission, Principal, Project, authorize
from agentrail_core.ids import new_sortable_id
from agentrail_core.trajectories import redact_payload

OUTBOX_EVENT_RUN_RESUMED = "evaluation_run.resumed"


async def principal_for_approval(
    session: AsyncSession, actor: Actor, approval_id: str
) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(ApprovalRequest, ApprovalRequest.project_id == Project.id)
        .where(ApprovalRequest.id == approval_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def list_run_approvals(
    session: AsyncSession,
    principal: Principal,
    *,
    run_id: str,
    state: ApprovalState | None = None,
) -> list[ApprovalRequest]:
    authorize(principal, Permission.APPROVAL_READ, organisation_id=principal.organisation_id)
    clauses = [
        ApprovalRequest.run_id == run_id,
        EvaluationRun.id == ApprovalRequest.run_id,
        Project.id == EvaluationRun.project_id,
        Project.organisation_id == principal.organisation_id,
    ]
    if state is not None:
        clauses.append(ApprovalRequest.state == state)
    rows = await session.scalars(
        select(ApprovalRequest)
        .join(EvaluationRun, EvaluationRun.id == ApprovalRequest.run_id)
        .join(Project, Project.id == EvaluationRun.project_id)
        .where(*clauses)
        .order_by(ApprovalRequest.created_at, ApprovalRequest.id)
    )
    return list(rows.all())


async def list_project_approvals(
    session: AsyncSession,
    principal: Principal,
    *,
    project_id: str,
    state: ApprovalState | None = None,
    limit: int = 50,
) -> list[ApprovalRequest]:
    """The reviewer's queue: everything waiting across one project.

    Listing per run is fine for a trace view, but a reviewer does not arrive
    knowing which run stopped — they arrive knowing something needs an answer.
    Ordered oldest first, because the thing that has been blocked longest is the
    thing most worth looking at.
    """
    authorize(principal, Permission.APPROVAL_READ, organisation_id=principal.organisation_id)
    clauses = [
        ApprovalRequest.project_id == project_id,
        Project.id == ApprovalRequest.project_id,
        Project.organisation_id == principal.organisation_id,
    ]
    if state is not None:
        clauses.append(ApprovalRequest.state == state)
    rows = await session.scalars(
        select(ApprovalRequest)
        .join(Project, Project.id == ApprovalRequest.project_id)
        .where(*clauses)
        .order_by(ApprovalRequest.created_at, ApprovalRequest.id)
        .limit(limit)
    )
    return list(rows.all())


async def get_approval(
    session: AsyncSession, principal: Principal, *, approval_id: str
) -> ApprovalRequest:
    authorize(principal, Permission.APPROVAL_READ, organisation_id=principal.organisation_id)
    approval = await session.scalar(
        select(ApprovalRequest)
        .join(Project, Project.id == ApprovalRequest.project_id)
        .where(
            ApprovalRequest.id == approval_id,
            Project.organisation_id == principal.organisation_id,
        )
    )
    if approval is None:
        raise ForbiddenError()
    return approval


async def decide_approval(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    approval_id: str,
    request: DecideApprovalRequest,
) -> ApprovalRequest:
    """Record a reviewer's answer, once.

    The row is locked and its state re-read inside this transaction, so two
    reviewers racing produce one decision and one `409` rather than a silent
    overwrite of somebody else's answer.
    """
    authorize(principal, Permission.APPROVAL_DECIDE, organisation_id=principal.organisation_id)
    approval = await session.scalar(
        select(ApprovalRequest)
        .join(Project, Project.id == ApprovalRequest.project_id)
        .where(
            ApprovalRequest.id == approval_id,
            Project.organisation_id == principal.organisation_id,
        )
        .with_for_update()
    )
    if approval is None:
        raise ForbiddenError()
    # The column is a plain string with a check constraint, like every other
    # state column here, so a loaded row carries a ``str`` rather than the enum
    # its annotation promises.
    current = ApprovalState(approval.state)
    if is_terminal_approval(current):
        # Deliberately not idempotent-success: re-deciding is a different
        # intent from retrying, and quietly returning the old answer would let a
        # reviewer believe they had overturned it.
        raise ConflictError(
            "This approval has already been decided.",
            details={"state": current.value, "decided_at": str(approval.decided_at)},
        )

    next_state = ApprovalState.APPROVED if request.approve else ApprovalState.REJECTED
    assert_approval_transition(current, next_state)

    edited, _summary = (
        redact_payload(request.edited_arguments)
        if request.edited_arguments is not None
        else (None, {})
    )
    approval.state = next_state
    approval.edited_arguments = edited
    approval.reason = request.reason
    approval.decided_by = actor.user.id if actor.user else None
    approval.decided_at = datetime.now(UTC)
    approval.updated_at = approval.decided_at
    approval.version += 1

    if request.approve:
        await _resume_item(session, approval=approval)
    else:
        await _fail_item(session, approval=approval)

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="approval.decided",
        target_type="approval_request",
        target_id=approval.id,
        context={
            "run_id": approval.run_id,
            "run_item_id": approval.run_item_id,
            "tool": approval.tool,
            "risk_level": approval.risk_level,
            "state": next_state.value,
            "edited": request.edited_arguments is not None,
        },
    )
    await session.flush()
    return approval


async def _resume_item(session: AsyncSession, *, approval: ApprovalRequest) -> None:
    """Put the item back in the queue's path.

    Conditional on the item still being parked: if the run was cancelled while
    the reviewer was deciding, this matches nothing and the approval stands as a
    record of the answer without resurrecting cancelled work.
    """
    resumed = await session.execute(
        update(RunItem)
        .where(
            RunItem.id == approval.run_item_id,
            RunItem.state == RunItemState.AWAITING_APPROVAL,
        )
        .values(state=RunItemState.PENDING, error_code=None, error_message=None)
    )
    if not resumed.rowcount:
        return
    # Published through the outbox, in this transaction, for the same reason run
    # creation is: the decision and its delivery commit together or not at all.
    session.add(
        OutboxEvent(
            id=new_sortable_id(),
            event_type=OUTBOX_EVENT_RUN_RESUMED,
            aggregate_type="evaluation_run",
            aggregate_id=approval.run_id,
            payload={"run_id": approval.run_id, "approval_id": approval.id},
        )
    )


async def _fail_item(session: AsyncSession, *, approval: ApprovalRequest) -> None:
    """A rejected action's item is terminal immediately.

    The worker would refuse it anyway — the gate re-reads the approval and finds
    a terminal state — but leaving it parked would keep a run open forever
    waiting on an answer that has already been given.
    """
    await session.execute(
        update(RunItem)
        .where(
            RunItem.id == approval.run_item_id,
            RunItem.state == RunItemState.AWAITING_APPROVAL,
        )
        .values(
            state=RunItemState.FAILED_TERMINAL,
            error_code="approval_rejected",
            error_message=f"Approval {approval.id} was rejected.",
            completed_at=datetime.now(UTC),
        )
    )
