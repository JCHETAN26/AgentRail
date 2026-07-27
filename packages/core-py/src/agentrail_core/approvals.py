"""Human approval of high-risk tool calls.

Phase 10's second exit criterion — a delayed event cannot bypass a rejection —
is the same shape as Phase 9's zero-duplicate rule, and gets the same treatment:
the database, not careful code, is what makes the bad outcome unrepresentable.

Two guards carry it:

* ``APPROVAL_TRANSITIONS`` has no outgoing edges from ``REJECTED``. A decision,
  once made, is final, so a late event cannot flip it.
* ``side_effect_records`` carries a ``CHECK`` requiring an ``approval_id``
  whenever ``required_approval`` is set, so a high-risk effect literally cannot
  be recorded without pointing at an approval row.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentrail_core.db import Base


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    #: The run was cancelled, or the item went terminal for another reason,
    #: before a reviewer answered. Distinct from REJECTED: nobody said no.
    WITHDRAWN = "WITHDRAWN"


TERMINAL_APPROVAL_STATES: frozenset[ApprovalState] = frozenset(
    {ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.WITHDRAWN}
)

APPROVAL_TRANSITIONS: dict[ApprovalState, frozenset[ApprovalState]] = {
    ApprovalState.PENDING: frozenset(
        {ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.WITHDRAWN}
    ),
    # No outgoing edges. A reviewer's answer is final; re-deciding means a new
    # request, which means a new row and a new audit trail.
    ApprovalState.APPROVED: frozenset(),
    ApprovalState.REJECTED: frozenset(),
    ApprovalState.WITHDRAWN: frozenset(),
}

_APPROVAL_STATES = ", ".join(f"'{state.value}'" for state in ApprovalState)


class IllegalApprovalTransitionError(Exception):
    def __init__(self, current: ApprovalState, requested: ApprovalState) -> None:
        super().__init__(f"Cannot transition approval from {current} to {requested}")
        self.current = current
        self.requested = requested


def can_transition_approval(current: ApprovalState, requested: ApprovalState) -> bool:
    return requested in APPROVAL_TRANSITIONS[current]


def assert_approval_transition(current: ApprovalState, requested: ApprovalState) -> None:
    if not can_transition_approval(current, requested):
        raise IllegalApprovalTransitionError(current, requested)


def is_terminal_approval(state: ApprovalState) -> bool:
    return state in TERMINAL_APPROVAL_STATES


class ApprovalRequest(Base):
    """One high-risk tool call, parked for a human."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(f"state IN ({_APPROVAL_STATES})", name="ck_approval_requests_state"),
        # One request per intended effect. The key is the same one the
        # side-effect ledger uses, so an item that is retried, redelivered or
        # picked up by a second worker asks the same question once rather than
        # spamming a reviewer with duplicates of it.
        UniqueConstraint("idempotency_key", name="uq_approval_requests_idempotency_key"),
        Index("ix_approval_requests_run_id", "run_id"),
        Index("ix_approval_requests_project_state", "project_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )
    run_item_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("run_items.id", ondelete="CASCADE"), nullable=False
    )
    trajectory_id: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("trajectories.id", ondelete="SET NULL"), nullable=True
    )
    #: The side-effect key this request authorises. Shared with the ledger.
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tool: Mapped[str] = mapped_column(String(128), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[ApprovalState] = mapped_column(
        String(16), nullable=False, default=ApprovalState.PENDING, server_default="PENDING"
    )
    #: Redacted before storage, like every other diagnostic payload.
    requested_arguments: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    #: Set when a reviewer approves *with changes*. Null on a plain approval.
    edited_arguments: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    @property
    def effective_arguments(self) -> dict[str, Any]:
        """What the executor should actually run with.

        An edit replaces the arguments wholesale rather than merging: a reviewer
        who removes a key means to remove it, and a merge would silently keep
        the original value.
        """
        return (
            self.edited_arguments
            if self.edited_arguments is not None
            else (self.requested_arguments)
        )
