"""Release policy and gate use cases.

The gate works with no GitHub integration configured at all. That is not a
convenience — it is the design. A team should be able to run this in CI, read
the verdict from the response body, and fail their own build on it, without
installing an app or granting anybody write access to their repository.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_api.auth.service import Actor, principal_for_organisation
from agentrail_api.identity.service import record_audit
from agentrail_api.release.schemas import CreateReleasePolicyRequest, EvaluateGateRequest
from agentrail_core.errors import ConflictError, ForbiddenError, ValidationFailedError
from agentrail_core.evaluators import ComparisonReport
from agentrail_core.execution import (
    TERMINAL_ITEM_STATES,
    TERMINAL_RUN_STATES,
    EvaluationRun,
    EvaluationRunState,
    RunItem,
    RunItemState,
)
from agentrail_core.github import (
    CheckConclusion,
    CheckRunPublisher,
    CheckRunRequest,
    annotations_from_violations,
)
from agentrail_core.identity import Permission, Principal, Project, authorize
from agentrail_core.ids import new_sortable_id
from agentrail_core.release import (
    GateEvaluation,
    GateOutcome,
    GitHubRepositoryBinding,
    ReleasePolicyError,
    ReleasePolicyRecord,
    evaluate_gate,
    parse_release_policy,
)

CHECK_RUN_NAME = "AgentRail / release gate"
POLICY_ANNOTATION_PATH = ".agentrail/release-policy.json"


def slugify(name: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in name.lower()).split())


def _digest(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def principal_for_policy(session: AsyncSession, actor: Actor, policy_id: str) -> Principal:
    row = await session.execute(
        select(Project.organisation_id)
        .join(ReleasePolicyRecord, ReleasePolicyRecord.project_id == Project.id)
        .where(ReleasePolicyRecord.id == policy_id)
    )
    organisation_id = row.scalar_one_or_none()
    if organisation_id is None:
        raise ForbiddenError()
    return await principal_for_organisation(session, actor, organisation_id)


async def create_policy(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    project_id: str,
    request: CreateReleasePolicyRequest,
) -> ReleasePolicyRecord:
    """Record a new, immutable version of a policy.

    Validated here rather than at gate time: a policy that cannot be evaluated
    would otherwise sit in the database looking like protection until the day it
    was needed.
    """
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)
    try:
        parse_release_policy(request.definition)
    except ReleasePolicyError as invalid:
        raise ValidationFailedError(
            "The release policy cannot be evaluated.", details={"reason": invalid.reason}
        ) from invalid

    slug = slugify(request.name)
    digest = _digest(request.definition)
    duplicate = await session.scalar(
        select(ReleasePolicyRecord.id).where(
            ReleasePolicyRecord.project_id == project_id,
            ReleasePolicyRecord.definition_digest == digest,
        )
    )
    if duplicate is not None:
        raise ConflictError(
            "An identical release policy already exists in this project.",
            details={"release_policy_id": duplicate},
        )

    latest = await session.scalar(
        select(func.max(ReleasePolicyRecord.version)).where(
            ReleasePolicyRecord.project_id == project_id,
            ReleasePolicyRecord.slug == slug,
        )
    )
    policy = ReleasePolicyRecord(
        id=new_sortable_id(),
        project_id=project_id,
        name=request.name,
        slug=slug,
        version=int(latest or 0) + 1,
        definition=request.definition,
        definition_digest=digest,
        created_by=actor.user.id if actor.user else None,
    )
    session.add(policy)
    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="release_policy.created",
        target_type="release_policy",
        target_id=policy.id,
        context={"project_id": project_id, "slug": slug, "version": policy.version},
    )
    await session.flush()
    return policy


async def list_policies(
    session: AsyncSession, principal: Principal, *, project_id: str
) -> list[ReleasePolicyRecord]:
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    rows = await session.scalars(
        select(ReleasePolicyRecord)
        .join(Project, Project.id == ReleasePolicyRecord.project_id)
        .where(
            ReleasePolicyRecord.project_id == project_id,
            Project.organisation_id == principal.organisation_id,
        )
        .order_by(ReleasePolicyRecord.slug, ReleasePolicyRecord.version.desc())
    )
    return list(rows.all())


async def evaluate_run_gate(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    run_id: str,
    request: EvaluateGateRequest,
    publisher: CheckRunPublisher | None = None,
) -> GateEvaluation:
    """Judge a finished run and record the verdict.

    Idempotent on (run, policy). A redelivered webhook or a retried CI step gets
    the recorded answer back rather than a freshly computed one, so a verdict
    cannot change after it has been reported.
    """
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)

    run = await session.scalar(
        select(EvaluationRun)
        .join(Project, Project.id == EvaluationRun.project_id)
        .where(
            EvaluationRun.id == run_id,
            Project.organisation_id == principal.organisation_id,
        )
    )
    if run is None:
        raise ForbiddenError()

    policy_record = await session.scalar(
        select(ReleasePolicyRecord).where(
            ReleasePolicyRecord.id == request.release_policy_id,
            ReleasePolicyRecord.project_id == run.project_id,
        )
    )
    if policy_record is None:
        raise ForbiddenError()

    existing = await session.scalar(
        select(GateEvaluation).where(
            GateEvaluation.run_id == run.id,
            GateEvaluation.release_policy_id == policy_record.id,
        )
    )
    if existing is not None:
        return existing

    report = await session.scalar(select(ComparisonReport).where(ComparisonReport.run_id == run.id))
    if report is None:
        # Deliberately a 409, not a 422: the request is well-formed, the run
        # simply has not produced a report yet. Telling CI "try again once the
        # run finishes" is different from "your request was wrong".
        raise ConflictError(
            "This run has no comparison report yet, so it cannot be gated.",
            details={"run_id": run.id, "run_state": str(run.state)},
        )

    policy = parse_release_policy(policy_record.definition)
    decision = evaluate_gate(
        policy,
        summary=report.summary,
        evaluator_metrics=report.evaluator_metrics,
        category_metrics=report.category_metrics,
    )

    head_sha = request.head_sha or run.github_head_sha

    # The row is reserved *before* anything is published. Publishing first would
    # let two concurrent callers both post a Check Run — the loser then discovers
    # the constraint and returns the winner's verdict, but its check has already
    # reached the pull request. Winning the insert is what earns the right to
    # speak.
    evaluation = GateEvaluation(
        id=new_sortable_id(),
        project_id=run.project_id,
        run_id=run.id,
        release_policy_id=policy_record.id,
        outcome=decision.outcome.value,
        violations=[violation.as_payload() for violation in decision.violations],
        summary=decision.summary_line()[:1024],
        head_sha=head_sha,
        check_run=None,
    )
    # Inside a SAVEPOINT so that losing the race rolls back only this insert. A
    # plain rollback would discard the audit record written below and leave the
    # caller's session unusable.
    try:
        async with session.begin_nested():
            session.add(evaluation)
            await session.flush()
    except IntegrityError:
        # Two callers raced on the unique (run, policy) pair. The loser reads
        # the winner's verdict, because the decision is a pure function of both
        # and cannot legitimately differ — and publishes nothing.
        winner = await session.scalar(
            select(GateEvaluation).where(
                GateEvaluation.run_id == run.id,
                GateEvaluation.release_policy_id == policy_record.id,
            )
        )
        if winner is None:  # pragma: no cover - only reachable if the row vanished
            raise
        return winner

    if publisher is not None and run.github_owner and run.github_repository and head_sha:
        evaluation.check_run = await publisher.publish(
            CheckRunRequest(
                owner=run.github_owner,
                repository=run.github_repository,
                head_sha=head_sha,
                name=CHECK_RUN_NAME,
                conclusion=(
                    CheckConclusion.FAILURE if decision.blocked else CheckConclusion.SUCCESS
                ),
                title=decision.summary_line(),
                summary=_check_summary(decision.as_payload(), policy_record),
                annotations=annotations_from_violations(
                    [violation.as_payload() for violation in decision.violations],
                    path=POLICY_ANNOTATION_PATH,
                ),
            )
        )

    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="release_gate.evaluated",
        target_type="gate_evaluation",
        target_id=evaluation.id,
        context={
            "run_id": run.id,
            "release_policy_id": policy_record.id,
            "outcome": decision.outcome.value,
            "violation_count": len(decision.violations),
            "head_sha": head_sha,
        },
    )
    await session.flush()
    return evaluation


async def assert_repository_claim(
    session: AsyncSession, *, project_id: str, owner: str | None, repository: str | None
) -> None:
    """Refuse provenance for a repository this project has not bound.

    Provenance is client-supplied, so without this a project could assert
    another tenant's coordinates and have that tenant's legitimate webhook
    cancel its runs.
    """
    if owner is None and repository is None:
        return
    if owner is None or repository is None:
        raise ValidationFailedError(
            "github_owner and github_repository must be given together.",
            details={"github_owner": owner, "github_repository": repository},
        )
    bound = await session.scalar(
        select(GitHubRepositoryBinding.id).where(
            GitHubRepositoryBinding.owner == owner,
            GitHubRepositoryBinding.repository == repository,
            GitHubRepositoryBinding.project_id == project_id,
        )
    )
    if bound is None:
        raise ForbiddenError()


async def create_repository_binding(
    session: AsyncSession,
    actor: Actor,
    principal: Principal,
    *,
    project_id: str,
    owner: str,
    repository: str,
) -> GitHubRepositoryBinding:
    """Claim a repository for this project. Exclusive and first-come."""
    authorize(principal, Permission.RUN_CREATE, organisation_id=principal.organisation_id)
    binding = GitHubRepositoryBinding(
        id=new_sortable_id(),
        project_id=project_id,
        owner=owner,
        repository=repository,
        created_by=actor.user.id if actor.user else None,
    )
    try:
        async with session.begin_nested():
            session.add(binding)
            await session.flush()
    except IntegrityError as clash:
        raise ConflictError(
            "That repository is already bound to a project.",
            details={"owner": owner, "repository": repository},
        ) from clash
    await record_audit(
        session,
        organisation_id=principal.organisation_id,
        actor=actor,
        action="github_binding.created",
        target_type="github_repository_binding",
        target_id=binding.id,
        context={"project_id": project_id, "owner": owner, "repository": repository},
    )
    await session.flush()
    return binding


async def list_repository_bindings(
    session: AsyncSession, principal: Principal, *, project_id: str
) -> list[GitHubRepositoryBinding]:
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    rows = await session.scalars(
        select(GitHubRepositoryBinding)
        .join(Project, Project.id == GitHubRepositoryBinding.project_id)
        .where(
            GitHubRepositoryBinding.project_id == project_id,
            Project.organisation_id == principal.organisation_id,
        )
        .order_by(GitHubRepositoryBinding.owner, GitHubRepositoryBinding.repository)
    )
    return list(rows.all())


async def list_run_gate_evaluations(
    session: AsyncSession, principal: Principal, *, run_id: str
) -> list[GateEvaluation]:
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    rows = await session.scalars(
        select(GateEvaluation)
        .join(Project, Project.id == GateEvaluation.project_id)
        .where(
            GateEvaluation.run_id == run_id,
            Project.organisation_id == principal.organisation_id,
        )
        .order_by(GateEvaluation.created_at, GateEvaluation.id)
    )
    return list(rows.all())


def _check_summary(decision: dict[str, Any], policy: ReleasePolicyRecord) -> str:
    """The body GitHub renders under the check title."""
    lines = [f"Judged against **{policy.name}** v{policy.version}.", ""]
    violations = decision.get("violations") or []
    if not violations:
        lines.append("Every release rule was satisfied.")
    else:
        lines.append("The following release rules were not met:")
        lines.append("")
        lines.extend(f"- {violation['message']}" for violation in violations)
    return "\n".join(lines)


def gate_outcome_is_blocked(evaluation: GateEvaluation) -> bool:
    return evaluation.outcome == GateOutcome.BLOCKED.value


async def cancel_superseded_runs(
    session: AsyncSession, *, owner: str, repository: str, pull_number: int, head_sha: str
) -> list[str]:
    """Cancel in-flight runs for earlier commits on the same pull request.

    A run judges one commit. When a new one is pushed, the older run's verdict
    describes code that is no longer under review — letting it finish would post
    a stale answer onto the pull request, and worse, one that might disagree with
    the current commit's.

    Runs for the *current* head are left alone, so a redelivered webhook for the
    same commit cancels nothing.

    Scoped to the project that has *bound* this repository. The webhook arrives
    at one URL under one deployment-wide secret and names a repository, not a
    tenant; without the binding it would cancel matching runs in every
    organisation, so any tenant could stop another's work by asserting the same
    coordinates on its own runs. No binding means no cancellation.
    """
    project_id = await session.scalar(
        select(GitHubRepositoryBinding.project_id).where(
            GitHubRepositoryBinding.owner == owner,
            GitHubRepositoryBinding.repository == repository,
        )
    )
    if project_id is None:
        return []

    runs = list(
        (
            await session.scalars(
                select(EvaluationRun)
                .where(
                    EvaluationRun.project_id == project_id,
                    EvaluationRun.github_owner == owner,
                    EvaluationRun.github_repository == repository,
                    EvaluationRun.github_pull_number == pull_number,
                    EvaluationRun.github_head_sha != head_sha,
                    EvaluationRun.state.not_in(tuple(state.value for state in TERMINAL_RUN_STATES)),
                )
                .with_for_update()
            )
        ).all()
    )
    cancelled: list[str] = []
    for run in runs:
        run.state = EvaluationRunState.CANCELLED
        run.cancelled_at = datetime.now(UTC)
        run.completed_at = run.cancelled_at
        run.updated_at = run.cancelled_at
        run.error_code = "superseded"
        run.error_message = f"Superseded by {head_sha[:12]} on pull request {pull_number}."
        run.version += 1
        await session.execute(
            update(RunItem)
            .where(
                RunItem.run_id == run.id,
                RunItem.state.not_in(tuple(state.value for state in TERMINAL_ITEM_STATES)),
            )
            .values(state=RunItemState.CANCELLED, completed_at=func.now(), updated_at=func.now())
        )
        cancelled.append(run.id)
    await session.flush()
    return cancelled
