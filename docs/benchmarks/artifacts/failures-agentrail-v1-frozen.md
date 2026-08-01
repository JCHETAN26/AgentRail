# AgentRail Failures Benchmark

- Seed: `agentrail-v1-frozen`
- Scenario count: `40`
- Raw artifact: `docs/benchmarks/artifacts/failures-agentrail-v1-frozen.json`
- Generated at: `2026-08-01T19:58:00+00:00`
- No frozen-test tuning: `true`

| Metric | Value | 95% CI |
| --- | ---: | ---: |
| task_success | 0.750 | 0.598 - 0.858 |
| programmatic_pass | 0.750 | 0.598 - 0.858 |
| tribunal_approval | 0.000 | 0.000 - 0.088 |
| tribunal_consensus | 0.250 | 0.142 - 0.402 |
| false_block | 0.750 | 0.598 - 0.858 |
| false_approve | 0.000 | 0.000 - 0.088 |
| release_gate_precision | 0.250 | 0.142 - 0.402 |
| release_gate_recall | 1.000 | 0.722 - 1.000 |
| duration_ms_mean | 11.4 | 9.2 - 13.6 |
| duration_ms_p95 | 18.0 | n/a |

## Confusion Matrix By Incident Family

| Family | True pass | True block | False block | False approve |
| --- | ---: | ---: | ---: | ---: |
| api_latency | 0 | 3 | 0 | 0 |
| auth_regression | 0 | 0 | 3 | 0 |
| cache_staleness | 0 | 0 | 3 | 0 |
| cost_spike | 0 | 0 | 3 | 0 |
| database_lock | 0 | 3 | 0 | 0 |
| deployment_rollback | 0 | 0 | 3 | 0 |
| misconfigured_quota | 0 | 0 | 3 | 0 |
| model_timeout | 0 | 0 | 3 | 0 |
| policy_violation | 0 | 2 | 0 | 0 |
| prompt_injection | 0 | 0 | 2 | 0 |
| queue_backlog | 0 | 0 | 2 | 0 |
| rate_limit | 0 | 0 | 2 | 0 |
| redis_restart | 0 | 2 | 0 | 0 |
| schema_drift | 0 | 0 | 2 | 0 |
| tool_loop | 0 | 0 | 2 | 0 |
| worker_termination | 0 | 0 | 2 | 0 |
