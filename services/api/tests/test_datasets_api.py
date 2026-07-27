"""Dataset and evaluation-suite integration tests."""

from __future__ import annotations

import pytest
from api_test_support import Tenant

from agentrail_core.ids import is_sortable_id

pytestmark = pytest.mark.integration


def dataset_content() -> str:
    return "\n".join(
        [
            '{"id":"latency-1","input":{"service":"checkout"},"expected":{"incident":"latency"},"partition":"dev"}',
            '{"id":"errors-1","input":{"service":"payments"},"expected":{"incident":"errors"},"partition":"test"}',
        ]
    )


async def create_dataset(tenant: Tenant, name: str = "CloudOps Eval Data") -> dict[str, object]:
    response = await tenant.client.post(
        f"/api/v1/projects/{tenant.project_id}/datasets",
        json={"name": name, "description": "Labelled CloudOps scenarios"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_dataset_version(tenant: Tenant, dataset_id: str) -> dict[str, object]:
    response = await tenant.client.post(
        f"/api/v1/datasets/{dataset_id}/versions",
        json={
            "input_format": "jsonl",
            "content": dataset_content(),
            "source_filename": "cloudops.jsonl",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestDatasets:
    async def test_creates_and_lists_project_datasets(self, tenant: Tenant) -> None:
        created = await create_dataset(tenant)

        assert is_sortable_id(str(created["id"]))
        assert created["project_id"] == tenant.project_id
        assert created["slug"] == "cloudops-eval-data"

        listed = await tenant.client.get(f"/api/v1/projects/{tenant.project_id}/datasets")

        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [created["id"]]

    async def test_duplicate_dataset_slug_is_rejected(self, tenant: Tenant) -> None:
        await create_dataset(tenant, "CloudOps Eval Data")

        duplicate = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/datasets",
            json={"name": "cloudops eval data"},
        )

        assert duplicate.status_code == 409

    async def test_cannot_create_dataset_in_another_tenants_project(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.post(
            f"/api/v1/projects/{other_tenant.project_id}/datasets",
            json={"name": "Intrusion"},
        )

        assert response.status_code == 403


class TestDatasetVersions:
    async def test_creates_immutable_dataset_version_with_validation_report(
        self, tenant: Tenant
    ) -> None:
        dataset = await create_dataset(tenant)
        version = await create_dataset_version(tenant, str(dataset["id"]))

        assert version["version"] == 1
        assert len(version["content_digest"]) == 64
        assert version["storage_uri"].startswith(f"s3://agentrail-datasets/{dataset['id']}/")
        assert version["item_count"] == 2
        assert version["rejected_count"] == 0
        assert version["partition_counts"] == {"dev": 1, "test": 1}
        assert version["schema"]["required"] == ["id", "input", "expected"]

        validation = await tenant.client.get(f"/api/v1/dataset-versions/{version['id']}/validation")

        assert validation.status_code == 200
        assert validation.json()["validation_report"]["accepted_count"] == 2

    async def test_malformed_dataset_is_actionable(self, tenant: Tenant) -> None:
        dataset = await create_dataset(tenant)

        response = await tenant.client.post(
            f"/api/v1/datasets/{dataset['id']}/versions",
            json={"input_format": "jsonl", "content": '{"id":"missing-fields"}\n{nope'},
        )

        assert response.status_code == 422
        details = response.json()["details"]
        assert details["rejected_count"] == 2
        assert details["rejections"][0]["line"] == 2
        assert details["rejections"][1]["fields"] == ["input", "expected"]

    async def test_duplicate_dataset_version_content_is_rejected(self, tenant: Tenant) -> None:
        dataset = await create_dataset(tenant)
        await create_dataset_version(tenant, str(dataset["id"]))

        duplicate = await tenant.client.post(
            f"/api/v1/datasets/{dataset['id']}/versions",
            json={"input_format": "jsonl", "content": dataset_content()},
        )

        assert duplicate.status_code == 409

    async def test_cannot_read_another_tenants_validation_report(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        dataset = await create_dataset(other_tenant, "Private Data")
        version = await create_dataset_version(other_tenant, str(dataset["id"]))

        response = await tenant.client.get(f"/api/v1/dataset-versions/{version['id']}/validation")

        assert response.status_code == 403
        assert str(version["content_digest"]) not in response.text


class TestEvaluationSuites:
    async def test_creates_suite_preview_and_freezes_it(self, tenant: Tenant) -> None:
        dataset = await create_dataset(tenant)
        version = await create_dataset_version(tenant, str(dataset["id"]))

        created = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/evaluation-suites",
            json={
                "name": "CloudOps Gate",
                "dataset_version_id": version["id"],
                "evaluators": [{"name": "exact_match", "version": "v1"}],
                "thresholds": {"task_success": 0.95},
                "fault_profiles": [{"kind": "tool.timeout", "attempts": [1]}],
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["frozen_at"] is None
        assert created.json()["preview"]["item_count"] == 2
        assert created.json()["preview"]["evaluator_count"] == 1

        frozen = await tenant.client.post(
            f"/api/v1/evaluation-suites/{created.json()['id']}/freeze"
        )
        frozen_again = await tenant.client.post(
            f"/api/v1/evaluation-suites/{created.json()['id']}/freeze"
        )

        assert frozen.status_code == 200
        assert frozen_again.status_code == 200
        assert frozen.json()["frozen_at"] is not None
        assert frozen_again.json()["frozen_at"] == frozen.json()["frozen_at"]

    async def test_rejects_a_suite_whose_fault_profile_cannot_be_executed(
        self, tenant: Tenant
    ) -> None:
        """Caught at the boundary, not when the worker parses it — a profile
        that only fails at run time strands a leased item and takes the
        consuming worker down with it."""
        dataset = await create_dataset(tenant)
        version = await create_dataset_version(tenant, str(dataset["id"]))

        response = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/evaluation-suites",
            json={
                "name": "Bad Profile Gate",
                "dataset_version_id": version["id"],
                "evaluators": [],
                "thresholds": {},
                "fault_profiles": [{"kind": "tool.timeout"}, {"name": "none"}],
            },
        )

        assert response.status_code == 422
        assert response.json()["code"] == "validation_failed"
        assert response.json()["details"]["index"] == 1

    async def test_cannot_build_suite_from_another_tenants_dataset_version(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        dataset = await create_dataset(other_tenant, "Private Suite Data")
        version = await create_dataset_version(other_tenant, str(dataset["id"]))

        response = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/evaluation-suites",
            json={"name": "Intrusion", "dataset_version_id": version["id"]},
        )

        assert response.status_code == 403
