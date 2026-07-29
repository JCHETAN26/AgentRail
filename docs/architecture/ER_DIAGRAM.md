# ER diagram

```mermaid
erDiagram
  organisations ||--o{ memberships : has
  users ||--o{ memberships : joins
  organisations ||--o{ projects : owns
  projects ||--o{ agent_definitions : contains
  agent_definitions ||--o{ agent_versions : versions
  projects ||--o{ datasets : contains
  datasets ||--o{ dataset_versions : versions
  dataset_versions ||--o{ evaluation_suites : feeds
  evaluation_suites ||--o{ evaluation_runs : creates
  agent_versions ||--o{ evaluation_runs : candidate
  evaluation_runs ||--o{ run_items : expands
  evaluation_runs ||--o{ outbox_events : publishes
  run_items ||--o{ trajectories : records
  trajectories ||--o{ trajectory_steps : contains
  trajectories ||--o{ trajectory_checkpoints : checkpoints
  trajectories ||--o{ trajectory_replays : replays
  evaluation_runs ||--o{ comparison_reports : summarizes
  evaluation_runs ||--o{ tribunal_sessions : judges
  tribunal_sessions ||--o{ tribunal_findings : writes
  tribunal_sessions ||--o{ tribunal_arguments : debates
  tribunal_sessions ||--o{ tribunal_verdicts : decides
  tribunal_sessions ||--o{ tribunal_replays : replays
  projects ||--o{ policy_bundles : defines
  projects ||--o{ release_policies : gates
  evaluation_runs ||--o{ approval_requests : pauses
  evaluation_runs ||--o{ deployment_records : promotes
  deployment_records ||--o{ deployment_events : tracks
```

## Notes

- Tenant ownership flows through `organisations` and `projects`; API reads verify tenant scope before
  returning resource details.
- Immutable evidence records use digests: agent versions, dataset versions, evaluator versions,
  prompt versions, comparison reports and replay records.
- Redis delivers work, but PostgreSQL owns state. `outbox_events` bridges that boundary.
- Tribunal state is scoped to an evaluation run; agents cannot share blackboard state across runs.
