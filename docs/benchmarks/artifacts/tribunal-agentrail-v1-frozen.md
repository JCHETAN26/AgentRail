# AgentRail Tribunal Benchmark

- Seed: `agentrail-v1-frozen`
- Scenario count: `128`
- Raw artifact: `docs/benchmarks/artifacts/tribunal-agentrail-v1-frozen.json`
- Generated at: `2026-07-29T02:58:54+00:00`
- No frozen-test tuning: `true`

| Metric | Value | 95% CI |
| --- | ---: | ---: |
| task_success | 0.859 | 0.789 - 0.909 |
| programmatic_pass | 0.781 | 0.702 - 0.844 |
| tribunal_approval | 0.758 | 0.677 - 0.824 |
| tribunal_consensus | 0.977 | 0.933 - 0.992 |
| false_block | 0.023 | 0.008 - 0.067 |
| false_approve | 0.000 | 0.000 - 0.029 |
| release_gate_precision | 0.903 | 0.751 - 0.967 |
| release_gate_recall | 1.000 | 0.879 - 1.000 |
| duration_ms_mean | 2589.2 | 2545.4 - 2633.1 |
| duration_ms_p95 | 2985.7 | n/a |

## Confusion Matrix By Incident Family

| Family | True pass | True block | False block | False approve |
| --- | ---: | ---: | ---: | ---: |
| api_latency | 6 | 2 | 0 | 0 |
| auth_regression | 5 | 2 | 1 | 0 |
| cache_staleness | 6 | 2 | 0 | 0 |
| cost_spike | 7 | 1 | 0 | 0 |
| database_lock | 4 | 3 | 1 | 0 |
| deployment_rollback | 7 | 1 | 0 | 0 |
| misconfigured_quota | 4 | 4 | 0 | 0 |
| model_timeout | 5 | 3 | 0 | 0 |
| policy_violation | 4 | 3 | 1 | 0 |
| prompt_injection | 6 | 2 | 0 | 0 |
| queue_backlog | 6 | 2 | 0 | 0 |
| rate_limit | 7 | 1 | 0 | 0 |
| redis_restart | 8 | 0 | 0 | 0 |
| schema_drift | 8 | 0 | 0 | 0 |
| tool_loop | 7 | 1 | 0 | 0 |
| worker_termination | 7 | 1 | 0 | 0 |
