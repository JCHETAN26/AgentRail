"""Trajectory trace explorer API tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api_test_support import Tenant, sign_in
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.execution import RunItem
from agentrail_core.identity import Role
from agentrail_core.ids import new_sortable_id
from agentrail_core.trajectories import (
    ReplayMode,
    Trajectory,
    TrajectoryCheckpoint,
    TrajectoryReplay,
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

    async def test_recorded_replay_reproduces_without_repeating_side_effects(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        trajectory_id = await attach_trajectory(session_factory, run)
        checkpoints = await tenant.client.get(f"/api/v1/trajectories/{trajectory_id}/checkpoints")
        checkpoint_id = checkpoints.json()["items"][0]["id"]

        created = await tenant.client.post(
            f"/api/v1/trajectories/{trajectory_id}/replays",
            json={"mode": ReplayMode.RECORDED, "checkpoint_id": checkpoint_id},
        )
        listed = await tenant.client.get(f"/api/v1/trajectories/{trajectory_id}/replays")

        assert created.status_code == 200
        body = created.json()
        assert body["result"]["reproduced"] is True
        assert body["source_digest"] == body["replay_digest"]
        assert body["safety_summary"]["original_side_effect_replayed"] is False
        assert body["safety_summary"]["replayed_side_effect_count"] == 0
        assert listed.status_code == 200
        assert listed.json()["items"][0]["id"] == body["id"]
        async with session_factory() as session:
            replay_count = await session.scalar(select(TrajectoryReplay))
        assert replay_count is not None

    async def test_forked_replay_records_divergence(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        trajectory_id = await attach_trajectory(session_factory, run)

        response = await tenant.client.post(
            f"/api/v1/trajectories/{trajectory_id}/replays",
            json={"mode": ReplayMode.FORKED, "fork_overrides": {"prompt": "try a safer plan"}},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["result"]["reproduced"] is False
        assert body["source_digest"] != body["replay_digest"]
        assert body["divergence"]["diverged"] is True
        assert body["divergence"]["changed_fields"] == ["prompt"]

    async def test_cannot_replay_another_tenants_trajectory(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run = await create_run(other_tenant)
        trajectory_id = await attach_trajectory(session_factory, run)

        response = await tenant.client.post(
            f"/api/v1/trajectories/{trajectory_id}/replays", json={"mode": ReplayMode.RECORDED}
        )

        assert response.status_code == 403

    async def test_a_viewer_can_read_replays_but_cannot_create_one(
        self,
        integration_app: FastAPI,
        tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Creating a replay writes a row and an audit event, so it needs
        ``run:create`` — reading every byte it derives from does not."""
        run = await create_run(tenant)
        trajectory_id = await attach_trajectory(session_factory, run)
        viewer = await sign_in(integration_app, "replay-viewer@example.com")
        try:
            granted = await tenant.client.post(
                f"/api/v1/organisations/{tenant.organisation_id}/members",
                json={"email": "replay-viewer@example.com", "role": Role.VIEWER.value},
            )
            assert granted.status_code == 201

            readable = await viewer.get(f"/api/v1/trajectories/{trajectory_id}/replays")
            writable = await viewer.post(
                f"/api/v1/trajectories/{trajectory_id}/replays",
                json={"mode": ReplayMode.RECORDED},
            )
        finally:
            await viewer.aclose()

        assert readable.status_code == 200
        assert writable.status_code == 403

    async def test_forks_differing_only_in_a_sensitive_value_do_not_share_a_digest(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Redacting before digesting would collapse these onto one digest and
        report no divergence between two materially different forks."""
        run = await create_run(tenant)
        trajectory_id = await attach_trajectory(session_factory, run)

        first = await tenant.client.post(
            f"/api/v1/trajectories/{trajectory_id}/replays",
            json={"mode": ReplayMode.FORKED, "fork_overrides": {"token": "alpha"}},
        )
        second = await tenant.client.post(
            f"/api/v1/trajectories/{trajectory_id}/replays",
            json={"mode": ReplayMode.FORKED, "fork_overrides": {"token": "beta"}},
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["replay_digest"] != second.json()["replay_digest"]
        assert first.json()["request"]["fork_overrides"]["token"] == "[REDACTED]"
        assert "alpha" not in first.text
        assert "beta" not in second.text

    async def test_recorded_replay_rejects_fork_overrides(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Silently ignoring them would record ``reproduced: true`` for a
        request that asked for something else."""
        run = await create_run(tenant)
        trajectory_id = await attach_trajectory(session_factory, run)

        response = await tenant.client.post(
            f"/api/v1/trajectories/{trajectory_id}/replays",
            json={"mode": ReplayMode.RECORDED, "fork_overrides": {"prompt": "different"}},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "validation_failed"
