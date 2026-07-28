"""Multi-agent Tribunal API tests."""

from __future__ import annotations

import pytest
from api_test_support import Tenant, sign_in
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.evaluators import ComparisonReport
from agentrail_core.identity import Role
from agentrail_core.ids import new_sortable_id
from agentrail_core.tribunal import TribunalReplay, TribunalSession
from services.api.tests.test_execution_api import create_agent_version, create_frozen_suite

pytestmark = pytest.mark.integration


async def create_run(
    tenant: Tenant, *, count: int = 16, thresholds: dict[str, object] | None = None
) -> dict[str, object]:
    suite = await create_frozen_suite(tenant, count=count, thresholds=thresholds)
    candidate = await create_agent_version(tenant, "Tribunal Candidate")
    response = await tenant.client.post(
        "/api/v1/evaluation-runs",
        json={
            "evaluation_suite_id": suite["id"],
            "candidate_agent_version_id": candidate["id"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def attach_comparison(
    session_factory: async_sessionmaker[AsyncSession],
    run: dict[str, object],
    *,
    reproducible: bool = True,
    pass_rate: float = 1.0,
) -> None:
    async with session_factory() as session:
        report = ComparisonReport(
            id=new_sortable_id(),
            project_id=str(run["project_id"]),
            run_id=str(run["id"]),
            baseline_agent_version_id=None,
            candidate_agent_version_id=str(run["candidate_agent_version_id"]),
            suite_digest="0" * 64,
            summary={
                "item_count": int(run["item_count"]),
                "result_count": int(run["item_count"]),
                "pass_rate": pass_rate,
                "regression_count": 0,
                "errors_in_denominator": True,
                "reproducible": reproducible,
            },
            evaluator_metrics={"task_success": {"total": 16, "passed": 16, "pass_rate": pass_rate}},
            category_metrics={"correctness": {"total": 16, "passed": 16, "pass_rate": pass_rate}},
            regressions=[],
            exports={"json": f"agentrail://evaluation-runs/{run['id']}/comparison"},
        )
        session.add(report)
        await session.commit()


class TestTribunalApi:
    async def test_creates_and_fetches_a_reproducible_tribunal_session(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_comparison(session_factory, run)

        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        replay = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        fetched = await tenant.client.get(f"/api/v1/evaluation-runs/{run['id']}/tribunal")

        assert created.status_code == 201, created.text
        assert replay.status_code == 200, replay.text
        assert fetched.status_code == 200, fetched.text
        assert replay.json()["id"] == created.json()["id"] == fetched.json()["id"]
        assert created.json()["outcome"] == "approved"
        assert created.json()["summary"]["agent_count"] == 6
        assert len(created.json()["findings"]) >= 5
        assert created.json()["verdict"]["outcome"] == "approved"
        assert [entry["sequence"] for entry in created.json()["blackboard"]] == list(
            range(1, len(created.json()["blackboard"]) + 1)
        )

    async def test_auditor_blocker_overrides_defender_approval(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_comparison(session_factory, run, reproducible=False)

        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")

        assert created.status_code == 201, created.text
        assert created.json()["outcome"] == "blocked"
        assert created.json()["verdict"]["dissent"]["defender_supported_approval"] is True
        assert created.json()["verdict"]["dissent"]["auditor_blockers"] == 1

    async def test_missing_comparison_blocks_approval(self, tenant: Tenant) -> None:
        run = await create_run(tenant)

        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")

        assert created.status_code == 201, created.text
        assert created.json()["outcome"] == "blocked"
        assert created.json()["summary"]["blocker_count"] == 1

    async def test_manual_creation_preserves_model_backed_suite_mode(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(
            tenant,
            thresholds={
                "task_success": 1.0,
                "tribunal": {
                    "enabled": True,
                    "mode": "model_backed",
                    "prompt_version": "tribunal-roles-v2",
                },
            },
        )
        await attach_comparison(session_factory, run)

        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")

        assert created.status_code == 201, created.text
        assert created.json()["summary"]["mode"] == "model_backed"
        assert created.json()["summary"]["prompt_version"] == "tribunal-roles-v2"
        assert created.json()["summary"]["model_call_count"] == 6

    async def test_openai_model_backed_tribunal_requires_server_credentials(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(
            tenant,
            thresholds={
                "task_success": 1.0,
                "tribunal": {
                    "enabled": True,
                    "mode": "model_backed",
                    "model_provider": "openai",
                    "model": "gpt-test",
                },
            },
        )
        await attach_comparison(session_factory, run)

        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")

        assert created.status_code == 422
        assert created.json()["code"] == "validation_failed"
        assert "OPENAI_API_KEY" in created.json()["details"]["reason"]

    async def test_cannot_read_or_create_another_tenants_tribunal(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run = await create_run(other_tenant)
        await attach_comparison(session_factory, run)

        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        fetched = await tenant.client.get(f"/api/v1/evaluation-runs/{run['id']}/tribunal")

        assert created.status_code == 403
        assert fetched.status_code == 403

        async with session_factory() as session:
            tribunal = await session.scalar(
                select(TribunalSession).where(TribunalSession.run_id == run["id"])
            )
        assert tribunal is None

    async def test_recorded_replay_hashes_the_actual_replayed_tribunal(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(
            tenant,
            thresholds={
                "task_success": 1.0,
                "tribunal": {
                    "enabled": True,
                    "mode": "model_backed",
                    "prompt_version": "tribunal-roles-v2",
                },
            },
        )
        await attach_comparison(session_factory, run)
        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        assert created.status_code == 201, created.text

        replay = await tenant.client.post(
            f"/api/v1/tribunal-sessions/{created.json()['id']}/replays",
            json={"mode": "recorded"},
        )
        listed = await tenant.client.get(
            f"/api/v1/tribunal-sessions/{created.json()['id']}/replays"
        )

        assert replay.status_code == 200, replay.text
        body = replay.json()
        assert body["outcome"] == created.json()["outcome"]
        assert body["result"]["source_outcome"] == created.json()["outcome"]
        assert body["result"]["replay_outcome"] == created.json()["outcome"]
        assert body["result"]["reproduced"] == (body["source_digest"] == body["replay_digest"])
        assert body["result"]["evidence"]["prompt_version"] == "tribunal-roles-v2"
        assert body["safety_summary"]["source_session_mutated"] is False
        assert body["safety_summary"]["live_model_calls"] == 0
        assert listed.status_code == 200
        assert listed.json()["items"][0]["id"] == body["id"]
        async with session_factory() as session:
            replay_row = await session.scalar(select(TribunalReplay))
        assert replay_row is not None

    async def test_forked_replay_records_prompt_and_model_override_divergence(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(
            tenant,
            thresholds={
                "task_success": 1.0,
                "tribunal": {"enabled": True, "mode": "model_backed"},
            },
        )
        await attach_comparison(session_factory, run)
        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        assert created.status_code == 201, created.text
        source_id = created.json()["id"]
        source_summary = created.json()["summary"]

        replay = await tenant.client.post(
            f"/api/v1/tribunal-sessions/{source_id}/replays",
            json={
                "mode": "forked",
                "prompt_version": "tribunal-roles-v3",
                "prompt_overrides": {
                    "defender": "Argue only from independently reproduced evidence."
                },
                "model_overrides": {"model": "tribunal-recorded-v2", "api_key": "secret"},
            },
        )
        fetched = await tenant.client.get(f"/api/v1/evaluation-runs/{run['id']}/tribunal")

        assert replay.status_code == 200, replay.text
        body = replay.json()
        assert body["result"]["reproduced"] is False
        assert body["source_digest"] != body["replay_digest"]
        assert body["divergence"]["diverged"] is True
        assert body["divergence"]["changed_fields"] == [
            "model_overrides",
            "prompt_overrides",
            "prompt_version",
        ]
        assert body["result"]["evidence"]["prompt_version"] == "tribunal-roles-v3"
        assert body["result"]["evidence"]["prompt_override_roles"] == ["defender"]
        assert body["request"]["model_overrides"]["api_key"] == "[REDACTED]"
        assert "secret" not in replay.text
        assert fetched.status_code == 200
        assert fetched.json()["summary"] == source_summary

    async def test_recorded_replay_rejects_fork_fields(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_comparison(session_factory, run)
        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        assert created.status_code == 201, created.text

        replay = await tenant.client.post(
            f"/api/v1/tribunal-sessions/{created.json()['id']}/replays",
            json={"mode": "recorded", "prompt_version": "tribunal-roles-v3"},
        )

        assert replay.status_code == 422
        assert replay.json()["code"] == "validation_failed"

    async def test_forked_replay_rejects_live_provider_override_alias(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_comparison(session_factory, run)
        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        assert created.status_code == 201, created.text

        replay = await tenant.client.post(
            f"/api/v1/tribunal-sessions/{created.json()['id']}/replays",
            json={"mode": "forked", "model_overrides": {"model_provider": "openai"}},
        )

        assert replay.status_code == 422
        assert replay.json()["code"] == "validation_failed"
        assert replay.json()["details"]["model_provider"] == "openai"

    async def test_cannot_replay_another_tenants_tribunal(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run = await create_run(other_tenant)
        await attach_comparison(session_factory, run)
        created = await other_tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        assert created.status_code == 201, created.text

        listed = await tenant.client.get(
            f"/api/v1/tribunal-sessions/{created.json()['id']}/replays"
        )
        replay = await tenant.client.post(
            f"/api/v1/tribunal-sessions/{created.json()['id']}/replays",
            json={"mode": "recorded"},
        )

        assert listed.status_code == 403
        assert replay.status_code == 403

    async def test_viewer_can_read_but_cannot_create_tribunal_replays(
        self,
        integration_app: FastAPI,
        tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run = await create_run(tenant)
        await attach_comparison(session_factory, run)
        created = await tenant.client.post(f"/api/v1/evaluation-runs/{run['id']}/tribunal")
        assert created.status_code == 201, created.text
        viewer = await sign_in(integration_app, "tribunal-replay-viewer@example.com")
        try:
            granted = await tenant.client.post(
                f"/api/v1/organisations/{tenant.organisation_id}/members",
                json={"email": "tribunal-replay-viewer@example.com", "role": Role.VIEWER.value},
            )
            assert granted.status_code == 201

            readable = await viewer.get(f"/api/v1/tribunal-sessions/{created.json()['id']}/replays")
            writable = await viewer.post(
                f"/api/v1/tribunal-sessions/{created.json()['id']}/replays",
                json={"mode": "recorded"},
            )
        finally:
            await viewer.aclose()

        assert readable.status_code == 200
        assert writable.status_code == 403
