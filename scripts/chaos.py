#!/usr/bin/env python
"""Force platform faults against a running stack and report what survived.

The unit and integration suites already prove the invariant in-process. This
exists for the other half of the question — whether the same thing holds when
the fault is a real duplicate Redis delivery or a real worker being killed,
against the real database, on someone's laptop.

It changes nothing about how the platform behaves: every fault here is a thing
that can happen in production anyway. Nothing in this file is imported by the
services.

    uv run python scripts/chaos.py duplicate-delivery --run-id <id>
    uv run python scripts/chaos.py strand-leases --run-id <id>
    uv run python scripts/chaos.py report --run-id <id>
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from agentrail_core.db import create_database_engine, create_session_factory
from agentrail_core.execution import RunItem, RunItemState
from agentrail_core.queue import create_redis_client, publish_job
from agentrail_core.settings import DatabaseSettings, QueueSettings
from agentrail_core.side_effects import SideEffectRecord


async def duplicate_delivery(run_id: str, times: int) -> int:
    """Publish the same run id repeatedly.

    Delivery is at-least-once by design, so this is not an attack — it is the
    normal failure mode, made to happen on demand.
    """
    queue_settings = QueueSettings()
    client = create_redis_client(queue_settings)
    try:
        for _ in range(times):
            await publish_job(client, queue_settings.run_queue_key, run_id)
    finally:
        await client.aclose()
    return times


async def strand_leases(run_id: str) -> int:
    """Expire every live lease on a run, as a killed worker would leave them."""
    settings = DatabaseSettings(service_name="agentrail-chaos")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            result = await session.execute(
                update(RunItem)
                .where(
                    RunItem.run_id == run_id,
                    RunItem.state.in_(
                        (
                            RunItemState.LEASED,
                            RunItemState.EXECUTING,
                            RunItemState.EVALUATING,
                        )
                    ),
                )
                .values(lease_expires_at=datetime.now(UTC) - timedelta(minutes=5))
            )
            await session.commit()
            return int(result.rowcount or 0)
    finally:
        await engine.dispose()


async def report(run_id: str) -> dict[str, object]:
    """The only number that matters: effects recorded versus items that ran."""
    settings = DatabaseSettings(service_name="agentrail-chaos")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            effects = await session.scalar(
                select(func.count())
                .select_from(SideEffectRecord)
                .where(SideEffectRecord.run_id == run_id)
            )
            distinct_keys = await session.scalar(
                select(func.count(func.distinct(SideEffectRecord.idempotency_key))).where(
                    SideEffectRecord.run_id == run_id
                )
            )
            attempts = await session.scalar(
                select(func.sum(RunItem.attempt_count)).where(RunItem.run_id == run_id)
            )
            states = {
                str(state): int(count)
                for state, count in (
                    await session.execute(
                        select(RunItem.state, func.count())
                        .where(RunItem.run_id == run_id)
                        .group_by(RunItem.state)
                    )
                ).all()
            }
        return {
            "run_id": run_id,
            "side_effects": int(effects or 0),
            "distinct_idempotency_keys": int(distinct_keys or 0),
            "total_attempts": int(attempts or 0),
            "item_states": states,
            # If these ever differ, the invariant this phase exists to hold has
            # been broken and the run is evidence worth keeping.
            "duplicate_side_effects": int(effects or 0) - int(distinct_keys or 0),
        }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("duplicate-delivery", "strand-leases", "report"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--times", type=int, default=3)
    args = parser.parse_args()

    if args.command == "duplicate-delivery":
        published = asyncio.run(duplicate_delivery(args.run_id, args.times))
        print(f"published run {args.run_id} {published} times")  # noqa: T201
        return 0
    if args.command == "strand-leases":
        stranded = asyncio.run(strand_leases(args.run_id))
        print(f"expired {stranded} leases on run {args.run_id}")  # noqa: T201
        return 0

    summary = asyncio.run(report(args.run_id))
    for key, value in summary.items():
        print(f"{key}: {value}")  # noqa: T201
    return 1 if summary["duplicate_side_effects"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
