# AgentRail Failures Benchmark

- Seed: `agentrail-v1-frozen`
- Scenario count: `24`
- Raw artifact: `docs/benchmarks/artifacts/failures-agentrail-v1-frozen.json`
- Generated at: `2026-08-01T00:19:30+00:00`
- No frozen-test tuning: `true`

| Metric | Value | 95% CI |
| --- | ---: | ---: |
| task_success | 0.750 | 0.551 - 0.880 |
| programmatic_pass | 0.750 | 0.551 - 0.880 |
| tribunal_approval | 0.000 | 0.000 - 0.138 |
| tribunal_consensus | 0.250 | 0.120 - 0.449 |
| false_block | 0.750 | 0.551 - 0.880 |
| false_approve | 0.000 | 0.000 - 0.138 |
| release_gate_precision | 0.250 | 0.120 - 0.449 |
| release_gate_recall | 1.000 | 0.610 - 1.000 |
| duration_ms_mean | 14.5 | 10.4 - 18.6 |
| duration_ms_p95 | 24.0 | n/a |

## Confusion Matrix By Incident Family

| Family | True pass | True block | False block | False approve |
| --- | ---: | ---: | ---: | ---: |
| api_latency | 0 | 2 | 0 | 0 |
| auth_regression | 0 | 0 | 2 | 0 |
| cache_staleness | 0 | 0 | 2 | 0 |
| cost_spike | 0 | 0 | 2 | 0 |
| database_lock | 0 | 2 | 0 | 0 |
| deployment_rollback | 0 | 0 | 2 | 0 |
| misconfigured_quota | 0 | 0 | 2 | 0 |
| model_timeout | 0 | 0 | 2 | 0 |
| policy_violation | 0 | 1 | 0 | 0 |
| prompt_injection | 0 | 0 | 1 | 0 |
| queue_backlog | 0 | 0 | 1 | 0 |
| rate_limit | 0 | 0 | 1 | 0 |
| redis_restart | 0 | 1 | 0 | 0 |
| schema_drift | 0 | 0 | 1 | 0 |
| tool_loop | 0 | 0 | 1 | 0 |
| worker_termination | 0 | 0 | 1 | 0 |
