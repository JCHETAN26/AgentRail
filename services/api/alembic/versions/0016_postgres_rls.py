"""Add tenant row-level security policies.

Revision ID: 0016_postgres_rls
Revises: 0015_tribunal_replays
Create Date: 2026-07-28
"""

# ruff: noqa: S608

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_postgres_rls"  # lgtm[py/unused-global-variable]
down_revision: str | None = "0015_tribunal_replays"  # lgtm[py/unused-global-variable]
branch_labels: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]
depends_on: str | Sequence[str] | None = None  # lgtm[py/unused-global-variable]

__all__ = ["branch_labels", "depends_on", "down_revision", "downgrade", "revision", "upgrade"]

POLICY_NAME = "agentrail_tenant_isolation"

DIRECT_ORGANISATION_TABLES = {
    "organisations": "id",
    "memberships": "organisation_id",
    "projects": "organisation_id",
    "api_keys": "organisation_id",
    "audit_events": "organisation_id",
    "organisation_quota_periods": "organisation_id",
}

DIRECT_PROJECT_TABLES = (
    "jobs",
    "agent_definitions",
    "datasets",
    "evaluation_suites",
    "evaluation_runs",
    "trajectories",
    "trajectory_replays",
    "evaluator_versions",
    "comparison_reports",
    "approval_requests",
    "release_policies",
    "gate_evaluations",
    "github_repository_bindings",
    "deployments",
    "side_effect_records",
    "tribunal_sessions",
    "tribunal_replays",
)

CHILD_PROJECT_TABLES = {
    "agent_versions": (
        "agent_id",
        "agent_definitions",
        "id",
        "project_id",
    ),
    "dataset_versions": ("dataset_id", "datasets", "id", "project_id"),
    "run_items": ("run_id", "evaluation_runs", "id", "project_id"),
    "evaluation_results": ("run_id", "evaluation_runs", "id", "project_id"),
    "trajectory_steps": ("trajectory_id", "trajectories", "id", "project_id"),
    "trajectory_checkpoints": ("trajectory_id", "trajectories", "id", "project_id"),
    "tribunal_blackboard_entries": ("session_id", "tribunal_sessions", "id", "project_id"),
    "tribunal_findings": ("session_id", "tribunal_sessions", "id", "project_id"),
    "tribunal_arguments": ("session_id", "tribunal_sessions", "id", "project_id"),
    "tribunal_verdicts": ("session_id", "tribunal_sessions", "id", "project_id"),
}


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION agentrail_current_organisation_id()
        RETURNS text
        LANGUAGE sql
        STABLE
        AS $$
            SELECT nullif(current_setting('agentrail.organisation_id', true), '')
        $$;
        """
    )
    for table, column in DIRECT_ORGANISATION_TABLES.items():
        _create_policy(
            table,
            f"{column} = agentrail_current_organisation_id()",
        )
    for table in DIRECT_PROJECT_TABLES:
        _create_policy(table, _project_predicate("project_id"))
    for table, (
        column,
        parent_table,
        parent_column,
        parent_project_column,
    ) in CHILD_PROJECT_TABLES.items():
        _create_policy(
            table,
            f"""
            EXISTS (
                SELECT 1
                FROM {parent_table}
                JOIN projects
                  ON projects.id = {parent_table}.{parent_project_column}
                WHERE {parent_table}.{parent_column} = {table}.{column}
                  AND projects.organisation_id = agentrail_current_organisation_id()
            )
            """,
        )


def downgrade() -> None:
    for table in [*CHILD_PROJECT_TABLES, *DIRECT_PROJECT_TABLES, *DIRECT_ORGANISATION_TABLES]:
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS agentrail_current_organisation_id()")


def _create_policy(table: str, predicate: str) -> None:
    scoped_predicate = f"""
    (
        agentrail_current_organisation_id() IS NULL
        OR {predicate}
    )
    """
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {POLICY_NAME}
        ON {table}
        USING ({scoped_predicate})
        WITH CHECK ({scoped_predicate})
        """
    )


def _project_predicate(column: str) -> str:
    return f"""
    EXISTS (
        SELECT 1
        FROM projects
        WHERE projects.id = {column}
          AND projects.organisation_id = agentrail_current_organisation_id()
    )
    """
