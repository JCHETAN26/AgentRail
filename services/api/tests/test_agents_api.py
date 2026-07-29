"""Agent registry integration tests."""

from __future__ import annotations

import pytest
from api_test_support import Tenant
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.identity import AgentVersion
from agentrail_core.ids import is_sortable_id

pytestmark = pytest.mark.integration


def version_payload(source_commit: str = "abc1234") -> dict[str, object]:
    return {
        "graph_spec": {"entrypoint": "diagnose", "nodes": ["diagnose", "report"]},
        "prompt_bundle": {"system": "Diagnose synthetic CloudOps incidents."},
        "model_config": {"provider": "recorded", "model": "cloudops-deterministic-v1"},
        "tool_contracts": [{"name": "get_service_health", "risk": "read"}],
        "policy_bundle": {"max_tool_calls": 8},
        "source_commit": source_commit,
    }


async def create_agent(tenant: Tenant, name: str = "CloudOps Agent") -> dict[str, object]:
    response = await tenant.client.post(
        f"/api/v1/projects/{tenant.project_id}/agents",
        json={"name": name, "description": "Synthetic incident investigator"},
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestAgentDefinitions:
    async def test_creates_and_lists_project_agents(self, tenant: Tenant) -> None:
        created = await create_agent(tenant)

        assert is_sortable_id(str(created["id"]))
        assert created["project_id"] == tenant.project_id
        assert created["slug"] == "cloudops-agent"

        listed = await tenant.client.get(f"/api/v1/projects/{tenant.project_id}/agents")

        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [created["id"]]

    async def test_duplicate_agent_slug_is_rejected(self, tenant: Tenant) -> None:
        await create_agent(tenant, "CloudOps Agent")

        duplicate = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/agents",
            json={"name": "cloudops agent"},
        )

        assert duplicate.status_code == 409

    async def test_cannot_create_agent_in_another_tenants_project(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.post(
            f"/api/v1/projects/{other_tenant.project_id}/agents",
            json={"name": "Intrusion"},
        )

        assert response.status_code == 403


class TestAgentVersions:
    async def test_creates_immutable_versions_with_stable_digests(self, tenant: Tenant) -> None:
        agent = await create_agent(tenant)

        first = await tenant.client.post(
            f"/api/v1/agents/{agent['id']}/versions", json=version_payload("abc1234")
        )
        second = await tenant.client.post(
            f"/api/v1/agents/{agent['id']}/versions", json=version_payload("def5678")
        )

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["version"] == 1
        assert second.json()["version"] == 2
        assert len(first.json()["content_digest"]) == 64
        assert first.json()["content_digest"] != second.json()["content_digest"]

        fetched = await tenant.client.get(f"/api/v1/agent-versions/{first.json()['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["content_digest"] == first.json()["content_digest"]

    async def test_duplicate_version_content_is_rejected(self, tenant: Tenant) -> None:
        agent = await create_agent(tenant)
        payload = version_payload()

        first = await tenant.client.post(f"/api/v1/agents/{agent['id']}/versions", json=payload)
        duplicate = await tenant.client.post(f"/api/v1/agents/{agent['id']}/versions", json=payload)

        assert first.status_code == 201
        assert duplicate.status_code == 409

    async def test_versions_cannot_be_mutated_after_creation(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        agent = await create_agent(tenant)
        created = (
            await tenant.client.post(
                f"/api/v1/agents/{agent['id']}/versions", json=version_payload()
            )
        ).json()

        async with session_factory() as session:
            version = await session.get(AgentVersion, created["id"])
            assert version is not None
            version.prompt_bundle = {"system": "mutated after creation"}

            with pytest.raises(ValueError, match="immutable after creation"):
                await session.commit()

        async with session_factory() as session:
            with pytest.raises(DBAPIError, match="immutable after creation"):
                await session.execute(
                    update(AgentVersion)
                    .where(AgentVersion.id == created["id"])
                    .values(prompt_bundle={"system": "bulk mutated after creation"})
                )
                await session.commit()

        fetched = await tenant.client.get(f"/api/v1/agent-versions/{created['id']}")

        assert fetched.status_code == 200
        assert fetched.json()["prompt_bundle"] == created["prompt_bundle"]

    async def test_lists_newest_version_first(self, tenant: Tenant) -> None:
        agent = await create_agent(tenant)
        first = (
            await tenant.client.post(
                f"/api/v1/agents/{agent['id']}/versions", json=version_payload("abc1234")
            )
        ).json()
        second = (
            await tenant.client.post(
                f"/api/v1/agents/{agent['id']}/versions", json=version_payload("def5678")
            )
        ).json()

        listed = await tenant.client.get(f"/api/v1/agents/{agent['id']}/versions")

        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [second["id"], first["id"]]

    async def test_cannot_read_another_tenants_agent_or_version(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        agent = await create_agent(other_tenant, "Private Agent")
        version = (
            await other_tenant.client.post(
                f"/api/v1/agents/{agent['id']}/versions", json=version_payload()
            )
        ).json()

        listed = await tenant.client.get(f"/api/v1/agents/{agent['id']}/versions")
        fetched = await tenant.client.get(f"/api/v1/agent-versions/{version['id']}")

        assert listed.status_code == 403
        assert fetched.status_code == 403
        assert "Private Agent" not in listed.text
        assert version["content_digest"] not in fetched.text
