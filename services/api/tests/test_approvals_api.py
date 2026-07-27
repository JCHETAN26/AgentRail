"""Approval endpoints, role enforcement and tenant isolation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api_test_support import Tenant, sign_in
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_core.approvals import ApprovalRequest, ApprovalState
from agentrail_core.execution import RunItem, RunItemState
from agentrail_core.identity import Role
from agentrail_core.ids import new_sortable_id
from services.api.tests.test_execution_api import create_agent_version, create_frozen_suite

pytestmark = pytest.mark.integration


async def create_parked_run(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[str, str]:
    """A run whose first item is parked on a pending approval."""
    suite = await create_frozen_suite(tenant, count=1)
    candidate = await create_agent_version(tenant, "Approval Candidate")
    created = await tenant.client.post(
        "/api/v1/evaluation-runs",
        json={
            "evaluation_suite_id": suite["id"],
            "candidate_agent_version_id": candidate["id"],
        },
    )
    assert created.status_code == 201, created.text
    run = created.json()

    async with session_factory() as session:
        item = await session.scalar(select(RunItem).where(RunItem.run_id == run["id"]))
        assert item is not None
        item.state = RunItemState.AWAITING_APPROVAL
        approval = ApprovalRequest(
            id=new_sortable_id(),
            project_id=str(run["project_id"]),
            run_id=str(run["id"]),
            run_item_id=item.id,
            idempotency_key=new_sortable_id() + "a" * 6,
            tool="restart_service",
            risk_level="HIGH_RISK_WRITE",
            state=ApprovalState.PENDING,
            requested_arguments={"service": "checkout", "api_key": "[REDACTED]"},
            created_at=datetime.now(UTC),
        )
        session.add(approval)
        await session.commit()
    return str(run["id"]), approval.id


class TestApprovalsApi:
    async def test_lists_and_fetches_a_pending_approval(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id, approval_id = await create_parked_run(tenant, session_factory)

        listed = await tenant.client.get(f"/api/v1/evaluation-runs/{run_id}/approvals")
        fetched = await tenant.client.get(f"/api/v1/approvals/{approval_id}")

        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [approval_id]
        assert fetched.status_code == 200
        assert fetched.json()["state"] == "PENDING"
        assert fetched.json()["risk_level"] == "HIGH_RISK_WRITE"

    async def test_approving_resumes_the_item_and_records_the_decision(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id, approval_id = await create_parked_run(tenant, session_factory)

        response = await tenant.client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"approve": True, "reason": "checked the runbook"},
        )

        assert response.status_code == 200
        assert response.json()["state"] == "APPROVED"
        assert response.json()["decided_by"] == tenant.user_id
        assert response.json()["decided_at"] is not None
        async with session_factory() as session:
            item = await session.scalar(select(RunItem).where(RunItem.run_id == run_id))
        assert item is not None
        assert item.state == RunItemState.PENDING, "an approved item goes back in the queue"

    async def test_rejecting_makes_the_item_terminal(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id, approval_id = await create_parked_run(tenant, session_factory)

        response = await tenant.client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"approve": False, "reason": "wrong service"},
        )

        assert response.status_code == 200
        assert response.json()["state"] == "REJECTED"
        async with session_factory() as session:
            item = await session.scalar(select(RunItem).where(RunItem.run_id == run_id))
        assert item is not None
        assert item.state == RunItemState.FAILED_TERMINAL
        assert item.error_code == "approval_rejected"

    async def test_a_decision_cannot_be_overturned(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Quietly returning the first answer would let a reviewer believe they
        had reversed it."""
        _run_id, approval_id = await create_parked_run(tenant, session_factory)
        await tenant.client.post(
            f"/api/v1/approvals/{approval_id}/decision", json={"approve": False}
        )

        second = await tenant.client.post(
            f"/api/v1/approvals/{approval_id}/decision", json={"approve": True}
        )

        assert second.status_code == 409
        assert second.json()["code"] == "conflict"
        assert second.json()["details"]["state"] == "REJECTED"

    async def test_edits_are_refused_on_a_rejection(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """There is nothing to edit about an action that will not run."""
        _run_id, approval_id = await create_parked_run(tenant, session_factory)

        response = await tenant.client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"approve": False, "edited_arguments": {"service": "other"}},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "validation_failed"

    async def test_a_viewer_can_see_an_approval_but_not_decide_it(
        self,
        integration_app: FastAPI,
        tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """The reviewer role finally means something."""
        run_id, approval_id = await create_parked_run(tenant, session_factory)
        viewer = await sign_in(integration_app, "approval-viewer@example.com")
        try:
            granted = await tenant.client.post(
                f"/api/v1/organisations/{tenant.organisation_id}/members",
                json={"email": "approval-viewer@example.com", "role": Role.VIEWER.value},
            )
            assert granted.status_code == 201

            readable = await viewer.get(f"/api/v1/evaluation-runs/{run_id}/approvals")
            writable = await viewer.post(
                f"/api/v1/approvals/{approval_id}/decision", json={"approve": True}
            )
        finally:
            await viewer.aclose()

        assert readable.status_code == 200
        assert writable.status_code == 403

    async def test_a_reviewer_can_decide(
        self,
        integration_app: FastAPI,
        tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        _run_id, approval_id = await create_parked_run(tenant, session_factory)
        reviewer = await sign_in(integration_app, "approval-reviewer@example.com")
        try:
            granted = await tenant.client.post(
                f"/api/v1/organisations/{tenant.organisation_id}/members",
                json={"email": "approval-reviewer@example.com", "role": Role.REVIEWER.value},
            )
            assert granted.status_code == 201

            response = await reviewer.post(
                f"/api/v1/approvals/{approval_id}/decision", json={"approve": True}
            )
        finally:
            await reviewer.aclose()

        assert response.status_code == 200
        assert response.json()["state"] == "APPROVED"

    async def test_lists_pending_approvals_across_a_project(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A reviewer arrives knowing something needs an answer, not which run
        stopped, so the queue is project-scoped rather than run-scoped."""
        _run_id, approval_id = await create_parked_run(tenant, session_factory)

        pending = await tenant.client.get(
            f"/api/v1/projects/{tenant.project_id}/approvals?state=PENDING"
        )
        decided = await tenant.client.get(
            f"/api/v1/projects/{tenant.project_id}/approvals?state=APPROVED"
        )

        assert pending.status_code == 200
        assert [item["id"] for item in pending.json()["items"]] == [approval_id]
        assert decided.status_code == 200
        assert decided.json()["items"] == []

    async def test_a_decided_approval_leaves_the_pending_queue(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        _run_id, approval_id = await create_parked_run(tenant, session_factory)
        await tenant.client.post(
            f"/api/v1/approvals/{approval_id}/decision", json={"approve": True}
        )

        pending = await tenant.client.get(
            f"/api/v1/projects/{tenant.project_id}/approvals?state=PENDING"
        )

        assert pending.json()["items"] == []

    async def test_cannot_list_another_tenants_project_queue(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.get(f"/api/v1/projects/{other_tenant.project_id}/approvals")

        assert response.status_code == 403

    async def test_cannot_read_or_decide_another_tenants_approval(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run_id, approval_id = await create_parked_run(other_tenant, session_factory)

        listed = await tenant.client.get(f"/api/v1/evaluation-runs/{run_id}/approvals")
        fetched = await tenant.client.get(f"/api/v1/approvals/{approval_id}")
        decided = await tenant.client.post(
            f"/api/v1/approvals/{approval_id}/decision", json={"approve": True}
        )

        assert listed.status_code == 403
        assert fetched.status_code == 403
        assert decided.status_code == 403
