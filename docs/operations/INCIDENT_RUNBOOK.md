# Evaluation Run Incident Runbook

Use this when a run is stuck, a release gate blocks unexpectedly, or a canary rolls back.

## First Five Minutes

1. Capture the `correlation_id` shown in the UI or error body.
2. Open `GET /api/v1/evaluation-runs/{run_id}/metrics`.
3. Confirm the `correlation.trace_id` matches logs for the API request and worker execution.
4. Check `queue.item_states`, `reliability.stranded_count` and `reliability.side_effect_count`.
5. Check `quality`, `release` and `canary` before deciding whether to retry, approve, promote or roll back.

## Decision Guide

| Symptom                                                 | First action                                                                         |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `stranded_count > 0`                                    | Inspect recovery view and let the worker reclaim expired leases.                     |
| `side_effect_count` exceeds completed side-effect steps | Stop retries and investigate idempotency keys.                                       |
| `release.blocked_count > 0`                             | Read gate violations before rerunning the candidate.                                 |
| `canary.rollback_count > 0`                             | Keep traffic at 0%, inspect `latest_deltas` and preserve the rollback reason.        |
| `slo.status = violated`                                 | Treat the run as not promotable until the listed objectives are satisfied or waived. |

## Evidence To Preserve

- `correlation_id`
- `trace_id`
- `run_id`
- release policy ID and gate verdict
- canary deployment ID and rollback reason
- failed trajectory IDs or failing item indices
