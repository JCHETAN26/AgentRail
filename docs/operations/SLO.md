# AgentRail SLOs

Phase 13 defines the first run-level operational objectives. They are evaluated from the same
records that power the API, worker, release gate and canary history; no dashboard-only number is
allowed to become the source of truth.

## Run Objectives

| Objective                 | Default            |
| ------------------------- | ------------------ |
| Minimum task success rate | `95%`              |
| Maximum failed items      | `0`                |
| Maximum stranded leases   | `0`                |
| Maximum canary rollbacks  | `0`                |
| Maximum recorded cost     | `5,000,000` micros |

`GET /api/v1/evaluation-runs/{run_id}/metrics` returns the current SLO verdict alongside the
underlying evidence. A violated objective should block promotion until the owner either fixes the
candidate or records an explicit exception in the release notes.

## Labels And Cardinality

Metrics must not use raw prompts, tool arguments, user email addresses or customer-provided strings
as labels. Use stable identifiers (`project_id`, `run_id`, evaluator slug, state, decision) and keep
high-cardinality evidence in structured records linked from the metric snapshot.
