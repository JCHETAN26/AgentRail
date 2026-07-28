"""Cross-tenant isolation.

The Phase 1 exit criterion: two organisations cannot access each other's data,
across every surface. Each test uses two independently provisioned tenants and
asserts that the second cannot reach the first's resources — by identifier,
which is the strongest form of the check, since the identifier is known.
"""

from __future__ import annotations

import pytest
from api_test_support import Tenant
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration


class TestOrganisationIsolation:
    async def test_listing_shows_only_your_own_organisations(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        listed = await tenant.client.get("/api/v1/organisations")

        ids = [item["id"] for item in listed.json()["items"]]
        assert ids == [tenant.organisation_id]
        assert other_tenant.organisation_id not in ids

    async def test_cannot_read_another_organisation_by_id(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.get(f"/api/v1/organisations/{other_tenant.organisation_id}")

        assert response.status_code == 403
        assert response.json()["code"] == "forbidden"

    async def test_cannot_list_another_organisations_members(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.get(
            f"/api/v1/organisations/{other_tenant.organisation_id}/members"
        )

        assert response.status_code == 403

    async def test_cannot_list_another_organisations_projects(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.get(
            f"/api/v1/organisations/{other_tenant.organisation_id}/projects"
        )

        assert response.status_code == 403

    async def test_cannot_create_a_project_in_another_organisation(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.post(
            f"/api/v1/organisations/{other_tenant.organisation_id}/projects",
            json={"name": "intrusion"},
        )

        assert response.status_code == 403

    async def test_cannot_read_another_organisations_audit_log(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.get(
            f"/api/v1/organisations/{other_tenant.organisation_id}/audit-events"
        )

        assert response.status_code == 403

    async def test_cannot_prune_another_organisations_audit_log(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.delete(
            f"/api/v1/organisations/{other_tenant.organisation_id}/audit-events/expired"
        )

        assert response.status_code == 403

    async def test_a_nonexistent_organisation_is_indistinguishable_from_someone_elses(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        """Otherwise the API is an oracle for which identifiers are real."""
        theirs = await tenant.client.get(f"/api/v1/organisations/{other_tenant.organisation_id}")
        imaginary = await tenant.client.get("/api/v1/organisations/01ARZ3NDEKTSV4RRFFQ69G5FAV")

        assert theirs.status_code == imaginary.status_code == 403
        assert theirs.json()["code"] == imaginary.json()["code"]
        assert theirs.json()["message"] == imaginary.json()["message"]


class TestJobIsolation:
    async def test_cannot_create_a_job_in_another_tenants_project(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.post(
            f"/api/v1/projects/{other_tenant.project_id}/jobs", json={"message": "intrusion"}
        )

        assert response.status_code == 403

    async def test_cannot_list_another_tenants_jobs(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        await other_tenant.client.post(
            f"/api/v1/projects/{other_tenant.project_id}/jobs", json={"message": "private"}
        )

        response = await tenant.client.get(f"/api/v1/projects/{other_tenant.project_id}/jobs")

        assert response.status_code == 403

    async def test_cannot_read_another_tenants_job_by_id(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        created = await other_tenant.client.post(
            f"/api/v1/projects/{other_tenant.project_id}/jobs", json={"message": "private"}
        )
        job_id = created.json()["id"]

        response = await tenant.client.get(f"/api/v1/jobs/{job_id}")

        assert response.status_code == 403
        assert "private" not in response.text

    async def test_job_listings_never_leak_across_projects(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/jobs", json={"message": "mine"}
        )
        await other_tenant.client.post(
            f"/api/v1/projects/{other_tenant.project_id}/jobs", json={"message": "theirs"}
        )

        mine = await tenant.client.get(f"/api/v1/projects/{tenant.project_id}/jobs")

        messages = [item["payload"]["message"] for item in mine.json()["items"]]
        assert messages == ["mine"]

    async def test_postgres_rls_filters_project_scoped_rows_when_context_is_set(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/jobs", json={"message": "mine"}
        )
        await other_tenant.client.post(
            f"/api/v1/projects/{other_tenant.project_id}/jobs", json={"message": "theirs"}
        )

        async with session_factory() as session:
            await session.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_roles WHERE rolname = 'agentrail_rls_probe'
                        ) THEN
                            CREATE ROLE agentrail_rls_probe NOLOGIN;
                        END IF;
                    END
                    $$;
                    """
                )
            )
            await session.execute(text("GRANT USAGE ON SCHEMA public TO agentrail_rls_probe"))
            await session.execute(
                text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO agentrail_rls_probe")
            )
            await session.execute(text("SET LOCAL ROLE agentrail_rls_probe"))
            await session.execute(
                text("SELECT set_config('agentrail.organisation_id', :org, true)"),
                {"org": tenant.organisation_id},
            )
            visible_projects = (
                await session.execute(text("SELECT id FROM projects ORDER BY id"))
            ).scalars()
            visible_jobs = (await session.execute(text("SELECT payload FROM jobs"))).scalars()

            assert list(visible_projects) == [tenant.project_id]
            assert [job["message"] for job in visible_jobs] == ["mine"]


class TestIdempotencyKeyScoping:
    async def test_two_tenants_may_use_the_same_idempotency_key(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        """Global uniqueness would let one tenant block another's key — and,
        worse, let them detect that it had been used."""
        headers = {"Idempotency-Key": "shared-key"}

        mine = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/jobs",
            json={"message": "mine"},
            headers=headers,
        )
        theirs = await other_tenant.client.post(
            f"/api/v1/projects/{other_tenant.project_id}/jobs",
            json={"message": "theirs"},
            headers=headers,
        )

        assert mine.status_code == 201
        assert theirs.status_code == 201
        assert mine.json()["id"] != theirs.json()["id"]

    async def test_replay_within_one_project_still_returns_the_original(
        self, tenant: Tenant
    ) -> None:
        headers = {"Idempotency-Key": "same-key"}
        first = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/jobs",
            json={"message": "hello"},
            headers=headers,
        )
        second = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/jobs",
            json={"message": "hello"},
            headers=headers,
        )

        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
