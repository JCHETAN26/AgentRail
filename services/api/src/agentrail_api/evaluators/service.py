"""Evaluator and comparison queries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentrail_core.errors import ForbiddenError
from agentrail_core.evaluators import ComparisonReport, EvaluationResult
from agentrail_core.execution import EvaluationRun
from agentrail_core.identity import Permission, Principal, Project, authorize


async def get_comparison_report(
    session: AsyncSession, principal: Principal, *, run_id: str
) -> ComparisonReport:
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    report = await session.scalar(
        select(ComparisonReport)
        .join(Project, Project.id == ComparisonReport.project_id)
        .where(
            ComparisonReport.run_id == run_id,
            Project.organisation_id == principal.organisation_id,
        )
    )
    if report is None:
        raise ForbiddenError()
    return report


async def get_baseline_report(
    session: AsyncSession, principal: Principal, *, report: ComparisonReport
) -> ComparisonReport | None:
    """Find the report this one should be measured against.

    The baseline is the most recent earlier report in the same project that
    scored the run's baseline agent version over the same suite. Requiring an
    identical ``suite_digest`` keeps the diff honest: metrics from a different
    evaluator set or thresholds are not comparable, so we report no baseline
    rather than subtracting unlike numbers.
    """
    if report.baseline_agent_version_id is None:
        return None
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    baseline: ComparisonReport | None = await session.scalar(
        select(ComparisonReport)
        .join(Project, Project.id == ComparisonReport.project_id)
        .where(
            ComparisonReport.project_id == report.project_id,
            Project.organisation_id == principal.organisation_id,
            ComparisonReport.candidate_agent_version_id == report.baseline_agent_version_id,
            ComparisonReport.suite_digest == report.suite_digest,
            ComparisonReport.run_id != report.run_id,
            ComparisonReport.id < report.id,
        )
        .order_by(ComparisonReport.id.desc())
        .limit(1)
    )
    return baseline


async def list_evaluation_results(
    session: AsyncSession,
    principal: Principal,
    *,
    run_id: str,
    evaluator_slug: str | None = None,
) -> list[EvaluationResult]:
    authorize(principal, Permission.RUN_READ, organisation_id=principal.organisation_id)
    clauses = [
        EvaluationResult.run_id == run_id,
        EvaluationRun.id == EvaluationResult.run_id,
        Project.id == EvaluationRun.project_id,
        Project.organisation_id == principal.organisation_id,
    ]
    if evaluator_slug is not None:
        clauses.append(EvaluationResult.evaluator_slug == evaluator_slug)
    rows = await session.scalars(
        select(EvaluationResult)
        .join(EvaluationRun, EvaluationRun.id == EvaluationResult.run_id)
        .join(Project, Project.id == EvaluationRun.project_id)
        .where(*clauses)
        .order_by(EvaluationResult.item_index, EvaluationResult.evaluator_slug)
    )
    return list(rows.all())
