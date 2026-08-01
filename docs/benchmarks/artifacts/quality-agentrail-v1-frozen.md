# AgentRail Quality Benchmark

- Seed: `agentrail-v1-frozen`
- Scenario count: `24`
- Raw artifact: `docs/benchmarks/artifacts/quality-agentrail-v1-frozen.json`
- Generated at: `2026-08-01T00:19:28+00:00`
- No frozen-test tuning: `true`

| Metric | Value | 95% CI |
| --- | ---: | ---: |
| task_success | 1.000 | 0.862 - 1.000 |
| programmatic_pass | 1.000 | 0.862 - 1.000 |
| tribunal_approval | 1.000 | 0.862 - 1.000 |
| tribunal_consensus | 1.000 | 0.862 - 1.000 |
| false_block | 0.000 | 0.000 - 0.138 |
| false_approve | 0.000 | 0.000 - 0.138 |
| release_gate_precision | 0.000 | 0.000 - 0.000 |
| release_gate_recall | 0.000 | 0.000 - 0.000 |
| duration_ms_mean | 23.0 | 18.4 - 27.5 |
| duration_ms_p95 | 40.1 | n/a |

## Confusion Matrix By Incident Family

| Family | True pass | True block | False block | False approve |
| --- | ---: | ---: | ---: | ---: |
| api_latency | 2 | 0 | 0 | 0 |
| auth_regression | 2 | 0 | 0 | 0 |
| cache_staleness | 2 | 0 | 0 | 0 |
| cost_spike | 2 | 0 | 0 | 0 |
| database_lock | 2 | 0 | 0 | 0 |
| deployment_rollback | 2 | 0 | 0 | 0 |
| misconfigured_quota | 2 | 0 | 0 | 0 |
| model_timeout | 2 | 0 | 0 | 0 |
| policy_violation | 1 | 0 | 0 | 0 |
| prompt_injection | 1 | 0 | 0 | 0 |
| queue_backlog | 1 | 0 | 0 | 0 |
| rate_limit | 1 | 0 | 0 | 0 |
| redis_restart | 1 | 0 | 0 | 0 |
| schema_drift | 1 | 0 | 0 | 0 |
| tool_loop | 1 | 0 | 0 | 0 |
| worker_termination | 1 | 0 | 0 | 0 |
