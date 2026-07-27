"""Trajectory trace explorer API tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api_test_support import Tenant
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.execution import RunItem
from agentrail_core.ids import new_sortable_id
from agentrail_core.trajectories import (
    Trajectory,
    TrajectoryCheckpoint,
    TrajectoryState,
    TrajectoryStep,
    TrajectoryStepType,
    redact_payload,
)
from services.api.tests.test_execution_api import create_agent_version, create_frozen_suite

pytestmark = pytest.mark.integration


async def create_run(tenant: Tenant) -> dict[str, object]:
    suite = await create_frozen_suite(tenant, count=1)
    candidate = await create_agent_version(tenant, "Trace Candidate")
    response = await tenant.client.post(
        "/api/v1/evaluation-runs",
        json={
            "evaluation_suite_id": suite["id"],
            "candidate_agent_version_id": candidate["id"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def attach_trajectory(
    session_factory: async_sessionmaker[AsyncSession], run: dict[str, object]
) -> str:
    async with session_factory() as session:
        item = await session.scalar(select(RunItem).where(RunItem.run_id == run["id"]))
        assert item is not None
        trajectory = Trajectory(
            id=new_sortable_id(),
            project_id=str(run["project_id"]),
            run_id=str(run["id"]),
            run_item_id=item.id,
            item_index=item.item_index,
            state=TrajectoryState.COMPLETED,
            summary={"result": "failed", "failing_step_id": None},
            graph_state={"node": "recorded_executor", "state": "failed"},
            final_checkpoint={"stage": "error"},
            completed_at=datetime.now(UTC),
        )
        session.add(trajectory)
        redacted_input, redaction_summary = redact_payload(
            {"arguments": {"service": "checkout", "api_key": "secret"}}
        )
        step = TrajectoryStep(
            id=new_sortable_id(),
            trajectory_id=trajectory.id,
            step_index=0,
            step_type=TrajectoryStepType.ERROR,
            title="Tool failure",
            redacted_input=redacted_input,
            redacted_output={"error": "timeout"},
            evidence={"log": "tool timed out"},
            checkpoint={"stage": "error"},
            redaction_summary={"input": redaction_summary},
        )
        session.add(step)
        await session.flush()
        trajectory.summary = {"result": "failed", "failing_step_id": step.id}
        session.add(
            TrajectoryCheckpoint(
                id=new_sortable_id(),
                trajectory_id=trajectory.id,
                step_id=step.id,
                checkpoint_index=0,
                label="failure",
                state={"stage": "error"},
            )
        )
        item.error_code = "tool_timeout"
        item.result = {"trajectory_id": trajectory.id, "failing_step_id": step.id}
        await session.commit()
        return trajectory.id


class TestTrajectoryApi:
    async def test_lists_failed_item_with_exact_step_and_redacted_payload(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        trajectory_id = await attach_trajectory(session_factory, run)

        items = await tenant.client.get(f"/api/v1/evaluation-runs/{run['id']}/items")
        trajectory = await tenant.client.get(f"/api/v1/trajectories/{trajectory_id}")
        steps = await tenant.client.get(f"/api/v1/trajectories/{trajectory_id}/steps")
        checkpoints = await tenant.client.get(f"/api/v1/trajectories/{trajectory_id}/checkpoints")

        assert items.status_code == 200
        assert items.json()["items"][0]["trajectory_id"] == trajectory_id
        assert items.json()["items"][0]["failing_step_id"] is not None
        assert trajectory.status_code == 200
        assert trajectory.json()["summary"]["result"] == "failed"
        assert steps.status_code == 200
        assert steps.json()["items"][0]["redacted_input"]["arguments"]["api_key"] == "[REDACTED]"
        assert "secret" not in steps.text
        assert checkpoints.status_code == 200
        assert checkpoints.json()["items"][0]["label"] == "failure"

    async def test_cannot_read_another_tenants_trajectory(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run = await create_run(other_tenant)
        trajectory_id = await attach_trajectory(session_factory, run)

        response = await tenant.client.get(f"/api/v1/trajectories/{trajectory_id}/steps")

        assert response.status_code == 403
        assert "secret" not in response.text
