# AgentRail Load Benchmark

- Seed: `agentrail-v1-frozen`
- Scenario count: `40`
- Raw artifact: `docs/benchmarks/artifacts/load-agentrail-v1-frozen.json`
- Generated at: `2026-08-01T19:58:04+00:00`
- No frozen-test tuning: `true`

| Metric | Value | 95% CI |
| --- | ---: | ---: |
| task_success | 1.000 | 0.912 - 1.000 |
| programmatic_pass | 1.000 | 0.912 - 1.000 |
| tribunal_approval | 1.000 | 0.912 - 1.000 |
| tribunal_consensus | 1.000 | 0.912 - 1.000 |
| false_block | 0.000 | 0.000 - 0.088 |
| false_approve | 0.000 | 0.000 - 0.088 |
| release_gate_precision | 0.000 | 0.000 - 0.000 |
| release_gate_recall | 0.000 | 0.000 - 0.000 |
| duration_ms_mean | 14.2 | 13.0 - 15.4 |
| duration_ms_p95 | 19.1 | n/a |

## Confusion Matrix By Incident Family

| Family | True pass | True block | False block | False approve |
| --- | ---: | ---: | ---: | ---: |
| api_latency | 3 | 0 | 0 | 0 |
| auth_regression | 3 | 0 | 0 | 0 |
| cache_staleness | 3 | 0 | 0 | 0 |
| cost_spike | 3 | 0 | 0 | 0 |
| database_lock | 3 | 0 | 0 | 0 |
| deployment_rollback | 3 | 0 | 0 | 0 |
| misconfigured_quota | 3 | 0 | 0 | 0 |
| model_timeout | 3 | 0 | 0 | 0 |
| policy_violation | 2 | 0 | 0 | 0 |
| prompt_injection | 2 | 0 | 0 | 0 |
| queue_backlog | 2 | 0 | 0 | 0 |
| rate_limit | 2 | 0 | 0 | 0 |
| redis_restart | 2 | 0 | 0 | 0 |
| schema_drift | 2 | 0 | 0 | 0 |
| tool_loop | 2 | 0 | 0 | 0 |
| worker_termination | 2 | 0 | 0 | 0 |
