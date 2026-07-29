"""Phase 11's exit criterion: a regressed pull request is blocked, a passing one succeeds."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from api_test_support import Tenant
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentrail_api.routers.integrations import _KNOWN_EVENTS, _known
from agentrail_core.evaluators import ComparisonReport
from agentrail_core.execution import EvaluationRun, EvaluationRunState
from agentrail_core.ids import new_sortable_id
from agentrail_core.tribunal import (
    TribunalSession,
    TribunalSessionState,
    TribunalVerdict,
    TribunalVerdictOutcome,
)
from services.api.tests.test_execution_api import create_agent_version, create_frozen_suite

pytestmark = pytest.mark.integration

WEBHOOK_SECRET = "test-webhook-secret"

STRICT_POLICY = {
    "min_pass_rate": 0.95,
    "max_regressions": 0,
    "min_evaluator_pass_rate": {"task_success": 0.95},
}
LENIENT_POLICY = {"min_pass_rate": 0.5, "max_regressions": 10}


async def create_run(tenant: Tenant) -> dict[str, Any]:
    suite = await create_frozen_suite(tenant, count=2)
    candidate = await create_agent_version(tenant, f"Gate Candidate {new_sortable_id()[:8]}")
    created = await tenant.client.post(
        "/api/v1/evaluation-runs",
        json={
            "evaluation_suite_id": suite["id"],
            "candidate_agent_version_id": candidate["id"],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


async def attach_report(
    session_factory: async_sessionmaker[AsyncSession],
    run: dict[str, Any],
    *,
    pass_rate: float,
    regressions: int,
    evaluator_pass_rate: float | None = None,
) -> None:
    """Give a run the comparison report the gate reads."""
    async with session_factory() as session:
        session.add(
            ComparisonReport(
                id=new_sortable_id(),
                project_id=str(run["project_id"]),
                run_id=str(run["id"]),
                candidate_agent_version_id=str(run["candidate_agent_version_id"]),
                suite_digest="d" * 64,
                summary={
                    "item_count": 2,
                    "result_count": 2,
                    "pass_rate": pass_rate,
                    "regression_count": regressions,
                    "errors_in_denominator": True,
                    "reproducible": True,
                },
                evaluator_metrics={
                    "task_success": {
                        "total": 2,
                        "passed": 2,
                        "failed": 0,
                        "errors": 0,
                        "pass_rate": (
                            pass_rate if evaluator_pass_rate is None else evaluator_pass_rate
                        ),
                        "mean_score": 1.0,
                    }
                },
                category_metrics={},
                regressions=[],
                exports={},
            )
        )
        await session.commit()


async def attach_tribunal(
    session_factory: async_sessionmaker[AsyncSession],
    run: dict[str, Any],
    *,
    outcome: TribunalVerdictOutcome,
) -> None:
    """Give a run the persisted Tribunal verdict the gate reads."""
    async with session_factory() as session:
        tribunal = TribunalSession(
            id=new_sortable_id(),
            project_id=str(run["project_id"]),
            run_id=str(run["id"]),
            state=TribunalSessionState.PUBLISHED,
            outcome=outcome,
            summary={"outcome": outcome.value},
        )
        verdict = TribunalVerdict(
            id=new_sortable_id(),
            session_id=tribunal.id,
            outcome=outcome,
            primary_reason=f"Test Tribunal verdict: {outcome.value}.",
            dissent={},
            evidence={},
        )
        session.add_all([tribunal, verdict])
        await session.commit()


async def bind_to_pull_request(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: str,
    *,
    owner: str,
    repository: str,
    pull_number: int,
    head_sha: str,
) -> None:
    async with session_factory() as session:
        run = await session.get(EvaluationRun, run_id)
        assert run is not None
        run.github_owner = owner
        run.github_repository = repository
        run.github_pull_number = pull_number
        run.github_head_sha = head_sha
        await session.commit()


async def bind_repository(
    tenant: Tenant, owner: str = "acme", repository: str = "agent"
) -> dict[str, Any]:
    response = await tenant.client.post(
        f"/api/v1/projects/{tenant.project_id}/github-repositories",
        json={"owner": owner, "repository": repository},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_policy(tenant: Tenant, name: str, definition: dict[str, Any]) -> dict[str, Any]:
    response = await tenant.client.post(
        f"/api/v1/projects/{tenant.project_id}/release-policies",
        json={"name": name, "definition": definition},
    )
    assert response.status_code == 201, response.text
    return response.json()


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def webhook_headers(
    body: bytes, *, event: str = "pull_request", delivery: str | None = None
) -> dict[str, str]:
    return {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery or new_sortable_id(),
        "X-Hub-Signature-256": sign(body),
    }


class TestReleasePolicies:
    async def test_creates_an_immutable_versioned_policy(self, tenant: Tenant) -> None:
        first = await create_policy(tenant, "Ship gate", STRICT_POLICY)
        second = await create_policy(tenant, "Ship gate", LENIENT_POLICY)

        assert first["version"] == 1
        assert second["version"] == 2, "a change is a new version, never an edit"
        assert first["slug"] == second["slug"] == "ship-gate"
        assert len(first["definition_digest"]) == 64

    async def test_an_identical_policy_is_rejected_rather_than_duplicated(
        self, tenant: Tenant
    ) -> None:
        await create_policy(tenant, "Ship gate", STRICT_POLICY)

        duplicate = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/release-policies",
            json={"name": "Another name", "definition": STRICT_POLICY},
        )

        assert duplicate.status_code == 409

    async def test_a_policy_that_forbids_nothing_is_refused_at_creation(
        self, tenant: Tenant
    ) -> None:
        """Otherwise it sits in the database looking like protection until the
        day it is needed."""
        response = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/release-policies",
            json={"name": "Empty", "definition": {"require_reproducible": True}},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "validation_failed"

    async def test_cannot_create_a_policy_in_another_tenants_project(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        response = await tenant.client.post(
            f"/api/v1/projects/{other_tenant.project_id}/release-policies",
            json={"name": "Intrusion", "definition": STRICT_POLICY},
        )

        assert response.status_code == 403


class TestTheGate:
    async def test_a_passing_candidate_succeeds(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_report(session_factory, run, pass_rate=0.98, regressions=0)
        policy = await create_policy(tenant, "Ship gate", STRICT_POLICY)

        response = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )

        assert response.status_code == 200
        assert response.json()["outcome"] == "passed"
        assert response.json()["violations"] == []
        assert response.json()["summary"] == "All release rules satisfied."

    async def test_a_regressed_candidate_is_blocked_and_says_why(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_report(session_factory, run, pass_rate=0.80, regressions=4)
        policy = await create_policy(tenant, "Ship gate", STRICT_POLICY)

        response = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "blocked"
        kinds = {violation["kind"] for violation in body["violations"]}
        assert kinds == {"min_pass_rate", "max_regressions", "min_evaluator_pass_rate"}
        assert all(
            "%" in violation["message"]
            for violation in body["violations"]
            if "rate" in violation["kind"]
        )

    async def test_required_tribunal_approval_waits_when_verdict_is_missing(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_report(session_factory, run, pass_rate=0.98, regressions=0)
        policy = await create_policy(
            tenant,
            "Tribunal required",
            {**LENIENT_POLICY, "require_tribunal_approval": True},
        )

        response = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )

        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "conflict"
        assert "no Tribunal verdict yet" in body["message"]

    async def test_required_tribunal_approval_blocks_non_approved_verdict(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_report(session_factory, run, pass_rate=0.98, regressions=0)
        await attach_tribunal(session_factory, run, outcome=TribunalVerdictOutcome.BLOCKED)
        policy = await create_policy(
            tenant,
            "Tribunal required",
            {**LENIENT_POLICY, "require_tribunal_approval": True},
        )

        response = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "blocked"
        assert body["violations"][0]["kind"] == "require_tribunal_approval"
        assert "blocked" in body["violations"][0]["message"]

    async def test_required_tribunal_approval_allows_approved_verdict(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run = await create_run(tenant)
        await attach_report(session_factory, run, pass_rate=0.98, regressions=0)
        await attach_tribunal(session_factory, run, outcome=TribunalVerdictOutcome.APPROVED)
        policy = await create_policy(
            tenant,
            "Tribunal required",
            {**LENIENT_POLICY, "require_tribunal_approval": True},
        )

        response = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )

        assert response.status_code == 200
        assert response.json()["outcome"] == "passed"

    async def test_gating_the_same_run_twice_returns_one_recorded_verdict(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A redelivered webhook or a retried CI step must not be able to
        produce a second, possibly different, answer."""
        run = await create_run(tenant)
        await attach_report(session_factory, run, pass_rate=0.98, regressions=0)
        policy = await create_policy(tenant, "Ship gate", STRICT_POLICY)

        first = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )
        second = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )

        assert first.json()["id"] == second.json()["id"]
        listed = await tenant.client.get(f"/api/v1/evaluation-runs/{run['id']}/gate")
        assert len(listed.json()["items"]) == 1

    async def test_a_run_with_no_report_yet_cannot_be_gated(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """'Try again once the run finishes' is a different answer from 'your
        request was wrong'."""
        run = await create_run(tenant)
        policy = await create_policy(tenant, "Ship gate", STRICT_POLICY)

        response = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )

        assert response.status_code == 409

    async def test_the_gate_works_with_no_github_configured(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """The whole point: CI reads the verdict from the response body without
        anybody installing an app or granting write access."""
        run = await create_run(tenant)
        await attach_report(session_factory, run, pass_rate=0.10, regressions=9)
        policy = await create_policy(tenant, "Ship gate", STRICT_POLICY)

        response = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )

        assert response.status_code == 200
        assert response.json()["outcome"] == "blocked"
        assert response.json()["check_run"] is None, "nothing was published anywhere"

    async def test_cannot_gate_another_tenants_run(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        run = await create_run(other_tenant)
        await attach_report(session_factory, run, pass_rate=0.98, regressions=0)
        policy = await create_policy(tenant, "Mine", STRICT_POLICY)

        response = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )

        assert response.status_code == 403


class TestPullRequestProvenance:
    async def test_a_run_started_by_ci_carries_its_pull_request(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Without this the release gate can never publish a Check Run and a new
        commit can never supersede the run it replaces."""
        await bind_repository(tenant)
        suite = await create_frozen_suite(tenant, count=1)
        candidate = await create_agent_version(tenant, "Provenance Candidate")

        created = await tenant.client.post(
            "/api/v1/evaluation-runs",
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
                "github_owner": "acme",
                "github_repository": "agent",
                "github_pull_number": 12,
                "github_head_sha": "e" * 40,
            },
        )

        assert created.status_code == 201, created.text
        async with session_factory() as session:
            run = await session.get(EvaluationRun, created.json()["id"])
        assert run is not None
        assert run.github_owner == "acme"
        assert run.github_pull_number == 12
        assert run.github_head_sha == "e" * 40

    async def test_a_console_run_needs_no_pull_request(self, tenant: Tenant) -> None:
        run = await create_run(tenant)

        assert run["id"]

    async def test_a_ci_run_publishes_a_check_and_a_console_run_does_not(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await bind_repository(tenant)
        suite = await create_frozen_suite(tenant, count=1)
        candidate = await create_agent_version(tenant, "Check Candidate")
        created = await tenant.client.post(
            "/api/v1/evaluation-runs",
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
                "github_owner": "acme",
                "github_repository": "agent",
                "github_pull_number": 13,
                "github_head_sha": "f" * 40,
            },
        )
        assert created.status_code == 201, created.text
        run = created.json()
        await attach_report(session_factory, run, pass_rate=0.10, regressions=9)
        policy = await create_policy(tenant, "Ship gate", STRICT_POLICY)

        response = await tenant.client.post(
            f"/api/v1/evaluation-runs/{run['id']}/gate",
            json={"release_policy_id": policy["id"]},
        )

        assert response.status_code == 200
        check = response.json()["check_run"]
        assert check is not None, "a pull-request run reports its verdict"
        assert check["delivered"] is False, "recorded, never delivered — no App client exists"
        assert check["check_run"]["conclusion"] == "failure"
        assert check["check_run"]["head_sha"] == "f" * 40
        assert check["check_run"]["details_url"].endswith(f"/runs/{run['id']}")
        assert len(check["check_run"]["output"]["annotations"]) == len(
            response.json()["violations"]
        )
        assert check["check_run"]["output"]["annotations"][0]["message"].count("Run evidence:") == 1


class TestRepositoryBindings:
    async def test_a_repository_can_only_be_claimed_once(self, tenant: Tenant) -> None:
        await bind_repository(tenant)

        duplicate = await tenant.client.post(
            f"/api/v1/projects/{tenant.project_id}/github-repositories",
            json={"owner": "acme", "repository": "agent"},
        )

        assert duplicate.status_code == 409

    async def test_another_tenant_cannot_claim_a_bound_repository(
        self, tenant: Tenant, other_tenant: Tenant
    ) -> None:
        await bind_repository(tenant)

        response = await other_tenant.client.post(
            f"/api/v1/projects/{other_tenant.project_id}/github-repositories",
            json={"owner": "acme", "repository": "agent"},
        )

        assert response.status_code == 409

    async def test_a_run_cannot_claim_a_repository_the_project_has_not_bound(
        self, tenant: Tenant
    ) -> None:
        """Provenance is client-supplied, so it has to be checked against a
        claim the project actually holds."""
        suite = await create_frozen_suite(tenant, count=1)
        candidate = await create_agent_version(tenant, "Unclaimed Candidate")

        response = await tenant.client.post(
            "/api/v1/evaluation-runs",
            json={
                "evaluation_suite_id": suite["id"],
                "candidate_agent_version_id": candidate["id"],
                "github_owner": "someone-else",
                "github_repository": "their-repo",
                "github_pull_number": 1,
                "github_head_sha": "a" * 40,
            },
        )

        assert response.status_code == 403


class TestWebhookLogSafety:
    """Nothing a caller writes may reach a log line.

    The logs are the record that holds an unauthenticated caller to account, so
    the caller must not be able to write to them.
    """

    def test_a_recognised_value_returns_our_own_constant(self) -> None:
        event = _known("pull_request", _KNOWN_EVENTS)

        assert event == "pull_request"
        # Equal as strings, but it must be *our* object, not the caller's —
        # that is the property that makes the log line ours to trust.
        assert any(event is member for member in _KNOWN_EVENTS)

    def test_an_unrecognised_value_never_reaches_the_log(self) -> None:
        forged = "pull_request\ninfo: forged entry that looks legitimate"

        assert _known(forged, _KNOWN_EVENTS) == "<unrecognised>"

    def test_an_absent_header_is_not_an_error(self) -> None:
        assert _known(None, _KNOWN_EVENTS) == "<unrecognised>"


class TestGitHubWebhook:
    async def test_an_unsigned_request_is_refused(self, tenant: Tenant) -> None:
        response = await tenant.client.post(
            "/api/v1/integrations/github/webhook",
            content=b'{"action":"synchronize"}',
            headers={"X-GitHub-Event": "pull_request"},
        )

        assert response.status_code == 401

    async def test_a_forged_signature_is_refused(self, tenant: Tenant) -> None:
        body = b'{"action":"synchronize"}'
        response = await tenant.client.post(
            "/api/v1/integrations/github/webhook",
            content=body,
            headers={
                **webhook_headers(body),
                "X-Hub-Signature-256": sign(body, secret="wrong-secret"),
            },
        )

        assert response.status_code == 401

    async def test_a_new_commit_cancels_the_run_for_the_previous_one(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """An older run's verdict describes code that is no longer under review."""
        await bind_repository(tenant)
        stale = await create_run(tenant)
        await bind_to_pull_request(
            session_factory,
            str(stale["id"]),
            owner="acme",
            repository="agent",
            pull_number=7,
            head_sha="a" * 40,
        )

        body = json.dumps(
            {
                "action": "synchronize",
                "pull_request": {"number": 7, "head": {"sha": "b" * 40}},
                "repository": {"name": "agent", "owner": {"login": "acme"}},
            }
        ).encode()
        response = await tenant.client.post(
            "/api/v1/integrations/github/webhook",
            content=body,
            headers=webhook_headers(body),
        )

        assert response.status_code == 202
        async with session_factory() as session:
            run = await session.get(EvaluationRun, str(stale["id"]))
        assert run is not None
        assert run.state == EvaluationRunState.CANCELLED
        assert run.error_code == "superseded"

    async def test_a_redelivery_for_the_same_commit_cancels_nothing(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await bind_repository(tenant)
        current = await create_run(tenant)
        await bind_to_pull_request(
            session_factory,
            str(current["id"]),
            owner="acme",
            repository="agent",
            pull_number=8,
            head_sha="c" * 40,
        )

        body = json.dumps(
            {
                "action": "synchronize",
                "pull_request": {"number": 8, "head": {"sha": "c" * 40}},
                "repository": {"name": "agent", "owner": {"login": "acme"}},
            }
        ).encode()
        await tenant.client.post(
            "/api/v1/integrations/github/webhook",
            content=body,
            headers=webhook_headers(body),
        )

        async with session_factory() as session:
            run = await session.get(EvaluationRun, str(current["id"]))
        assert run is not None
        assert run.state != EvaluationRunState.CANCELLED

    async def test_a_webhook_cannot_cancel_another_tenants_runs(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """The webhook arrives under one deployment-wide secret and names a
        repository, not a tenant. Without the binding it would cancel matching
        runs everywhere, letting any tenant stop another's work by asserting the
        same coordinates."""
        await bind_repository(tenant, owner="acme", repository="agent")
        mine = await create_run(tenant)
        await bind_to_pull_request(
            session_factory,
            str(mine["id"]),
            owner="acme",
            repository="agent",
            pull_number=9,
            head_sha="a" * 40,
        )
        # The other tenant asserts the same coordinates directly in the database,
        # bypassing the claim check, to prove the cancellation query itself is
        # scoped rather than merely the write path.
        theirs = await create_run(other_tenant)
        await bind_to_pull_request(
            session_factory,
            str(theirs["id"]),
            owner="acme",
            repository="agent",
            pull_number=9,
            head_sha="a" * 40,
        )

        body = json.dumps(
            {
                "action": "synchronize",
                "pull_request": {"number": 9, "head": {"sha": "b" * 40}},
                "repository": {"name": "agent", "owner": {"login": "acme"}},
            }
        ).encode()
        await tenant.client.post(
            "/api/v1/integrations/github/webhook",
            content=body,
            headers=webhook_headers(body),
        )

        async with session_factory() as session:
            ours = await session.get(EvaluationRun, str(mine["id"]))
            others = await session.get(EvaluationRun, str(theirs["id"]))
        assert ours is not None and others is not None
        assert ours.state == EvaluationRunState.CANCELLED, "the bound project's run is superseded"
        assert others.state != EvaluationRunState.CANCELLED, "another tenant's run is untouched"

    async def test_a_webhook_for_an_unbound_repository_cancels_nothing(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Fail closed: no binding means no tenant to resolve."""
        run = await create_run(tenant)
        await bind_to_pull_request(
            session_factory,
            str(run["id"]),
            owner="unclaimed",
            repository="repo",
            pull_number=3,
            head_sha="a" * 40,
        )

        body = json.dumps(
            {
                "action": "synchronize",
                "pull_request": {"number": 3, "head": {"sha": "b" * 40}},
                "repository": {"name": "repo", "owner": {"login": "unclaimed"}},
            }
        ).encode()
        await tenant.client.post(
            "/api/v1/integrations/github/webhook",
            content=body,
            headers=webhook_headers(body),
        )

        async with session_factory() as session:
            unchanged = await session.get(EvaluationRun, str(run["id"]))
        assert unchanged is not None
        assert unchanged.state != EvaluationRunState.CANCELLED

    async def test_an_unrelated_event_is_acknowledged_and_ignored(self, tenant: Tenant) -> None:
        """Returning an error would make GitHub retry it forever."""
        body = b'{"zen":"Design for failure."}'
        response = await tenant.client.post(
            "/api/v1/integrations/github/webhook",
            content=body,
            headers=webhook_headers(body, event="ping"),
        )

        assert response.status_code == 202

    async def test_a_replayed_delivery_is_rejected_before_side_effects(
        self, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await bind_repository(tenant)
        stale = await create_run(tenant)
        await bind_to_pull_request(
            session_factory,
            str(stale["id"]),
            owner="acme",
            repository="agent",
            pull_number=11,
            head_sha="a" * 40,
        )

        body = json.dumps(
            {
                "action": "synchronize",
                "pull_request": {"number": 11, "head": {"sha": "b" * 40}},
                "repository": {"name": "agent", "owner": {"login": "acme"}},
            }
        ).encode()
        headers = webhook_headers(body, delivery=f"delivery-{new_sortable_id()}")

        first = await tenant.client.post(
            "/api/v1/integrations/github/webhook", content=body, headers=headers
        )
        second = await tenant.client.post(
            "/api/v1/integrations/github/webhook", content=body, headers=headers
        )

        assert first.status_code == 202
        assert second.status_code == 409
        assert second.json()["code"] == "replayed_webhook"

    async def test_a_failed_webhook_release_allows_github_retry(
        self,
        tenant: Tenant,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await bind_repository(tenant)
        stale = await create_run(tenant)
        await bind_to_pull_request(
            session_factory,
            str(stale["id"]),
            owner="acme",
            repository="agent",
            pull_number=12,
            head_sha="a" * 40,
        )

        async def fail_after_reservation(*args: object, **kwargs: object) -> None:
            raise RuntimeError("postgres unavailable")

        monkeypatch.setattr(
            "agentrail_api.routers.integrations.service.cancel_superseded_runs",
            fail_after_reservation,
        )

        body = json.dumps(
            {
                "action": "synchronize",
                "pull_request": {"number": 12, "head": {"sha": "b" * 40}},
                "repository": {"name": "agent", "owner": {"login": "acme"}},
            }
        ).encode()
        headers = webhook_headers(body, delivery=f"transient-failure-{new_sortable_id()}")

        with pytest.raises(RuntimeError, match="postgres unavailable"):
            await tenant.client.post(
                "/api/v1/integrations/github/webhook", content=body, headers=headers
            )
        with pytest.raises(RuntimeError, match="postgres unavailable"):
            await tenant.client.post(
                "/api/v1/integrations/github/webhook", content=body, headers=headers
            )
