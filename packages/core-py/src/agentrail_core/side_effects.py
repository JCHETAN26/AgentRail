"""The side-effect ledger.

Phase 9's exit criterion is that forced failures and retries produce **zero
duplicate side effects**. That is enforced here by a ``UNIQUE`` constraint on a
deterministic idempotency key rather than by careful code alone: a second
attempt at the same effect violates the constraint and cannot commit, so a
duplicate is impossible at the database level even if a caller forgets to check
first, two workers race, or a lease expires mid-write.

The key deliberately excludes the attempt number. It has to be stable *across*
attempts — a key that varied per attempt would let every retry insert a fresh
row, which is the exact bug this table exists to make unrepresentable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from agentrail_core.db import Base
from agentrail_core.ids import new_sortable_id


def side_effect_key(
    *, run_item_id: str, step_index: int, tool: str, arguments: dict[str, Any]
) -> str:
    """Derive the stable idempotency key for one side-effecting tool call.

    Note what is *not* in here: the attempt number, the worker id, and the
    wall clock. Two attempts at the same call by different workers at different
    times must collide, because they represent one intended effect on the world.
    """
    payload = json.dumps(
        {
            "run_item_id": run_item_id,
            "step_index": step_index,
            "tool": tool,
            "arguments": arguments,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SideEffectRecord(Base):
    """One effect that reached the world, recorded exactly once."""

    __tablename__ = "side_effect_records"
    __table_args__ = (
        # The whole invariant, in one line.
        UniqueConstraint("idempotency_key", name="uq_side_effect_records_idempotency_key"),
        Index("ix_side_effect_records_run_id", "run_id"),
        Index("ix_side_effect_records_run_item_id", "run_item_id"),
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
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tool: Mapped[str] = mapped_column(String(128), nullable=False)
    arguments_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Which attempt actually applied the effect. Later attempts read the row
    #: rather than writing one, so this stays at the attempt that won.
    applied_on_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Redacted before it gets here, like every other diagnostic payload.
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


async def apply_side_effect_once(
    session: AsyncSession,
    *,
    project_id: str,
    run_id: str,
    run_item_id: str,
    step_index: int,
    tool: str,
    arguments: dict[str, Any],
    attempt: int,
    result: dict[str, Any],
) -> tuple[SideEffectRecord, bool]:
    """Record an effect, or discover it already happened.

    Returns the ledger row and whether *this* call was the one that applied it.
    A ``False`` means an earlier attempt already reached the world and the
    caller must reuse the recorded result rather than acting again.

    Three things can race here: two workers holding the same item after a lease
    expiry, a duplicate queue delivery, and a retry after a partial failure. The
    read below catches the common case cheaply; the constraint catches the rest.
    The insert runs inside a SAVEPOINT so losing that race rolls back only the
    insert, leaving the caller's surrounding transaction — trajectory steps,
    state transitions — intact.
    """
    key = side_effect_key(
        run_item_id=run_item_id, step_index=step_index, tool=tool, arguments=arguments
    )
    existing = await session.scalar(
        select(SideEffectRecord).where(SideEffectRecord.idempotency_key == key)
    )
    if existing is not None:
        return existing, False

    record = SideEffectRecord(
        id=new_sortable_id(),
        project_id=project_id,
        run_id=run_id,
        run_item_id=run_item_id,
        idempotency_key=key,
        tool=tool,
        arguments_digest=_digest(arguments),
        applied_on_attempt=attempt,
        result=result,
    )
    try:
        async with session.begin_nested():
            session.add(record)
            await session.flush()
    except IntegrityError:
        winner = await session.scalar(
            select(SideEffectRecord).where(SideEffectRecord.idempotency_key == key)
        )
        if winner is None:  # pragma: no cover - only reachable if the row vanished
            raise
        return winner, False
    return record, True


def _digest(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
