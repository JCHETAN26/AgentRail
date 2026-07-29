# AgentRail Smoke Benchmark

- Seed: `agentrail-v1-frozen`
- Scenario count: `32`
- Raw artifact: `docs/benchmarks/artifacts/smoke-agentrail-v1-frozen.json`
- Generated at: `2026-07-29T02:58:54+00:00`
- No frozen-test tuning: `true`

| Metric | Value | 95% CI |
| --- | ---: | ---: |
| task_success | 0.938 | 0.799 - 0.983 |
| programmatic_pass | 0.844 | 0.682 - 0.931 |
| tribunal_approval | 0.781 | 0.612 - 0.890 |
| tribunal_consensus | 0.875 | 0.719 - 0.950 |
| false_block | 0.094 | 0.032 - 0.242 |
| false_approve | 0.031 | 0.006 - 0.157 |
| release_gate_precision | 0.571 | 0.250 - 0.842 |
| release_gate_recall | 0.800 | 0.376 - 0.964 |
| duration_ms_mean | 1157.3 | 1015.6 - 1299.1 |
| duration_ms_p95 | 1850.7 | n/a |

## Confusion Matrix By Incident Family

| Family | True pass | True block | False block | False approve |
| --- | ---: | ---: | ---: | ---: |
| api_latency | 0 | 2 | 0 | 0 |
| auth_regression | 2 | 0 | 0 | 0 |
| cache_staleness | 1 | 0 | 0 | 1 |
| cost_spike | 2 | 0 | 0 | 0 |
| database_lock | 1 | 1 | 0 | 0 |
| deployment_rollback | 2 | 0 | 0 | 0 |
| misconfigured_quota | 2 | 0 | 0 | 0 |
| model_timeout | 2 | 0 | 0 | 0 |
| policy_violation | 2 | 0 | 0 | 0 |
| prompt_injection | 2 | 0 | 0 | 0 |
| queue_backlog | 1 | 1 | 0 | 0 |
| rate_limit | 2 | 0 | 0 | 0 |
| redis_restart | 1 | 0 | 1 | 0 |
| schema_drift | 1 | 0 | 1 | 0 |
| tool_loop | 2 | 0 | 0 | 0 |
| worker_termination | 1 | 0 | 1 | 0 |
