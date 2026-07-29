# AgentRail Load Benchmark

- Seed: `agentrail-v1-frozen`
- Scenario count: `96`
- Raw artifact: `docs/benchmarks/artifacts/load-agentrail-v1-frozen.json`
- Generated at: `2026-07-29T02:58:54+00:00`
- No frozen-test tuning: `true`

| Metric | Value | 95% CI |
| --- | ---: | ---: |
| task_success | 0.854 | 0.770 - 0.911 |
| programmatic_pass | 0.781 | 0.689 - 0.852 |
| tribunal_approval | 0.740 | 0.644 - 0.817 |
| tribunal_consensus | 0.917 | 0.844 - 0.957 |
| false_block | 0.062 | 0.029 - 0.130 |
| false_approve | 0.021 | 0.006 - 0.073 |
| release_gate_precision | 0.760 | 0.566 - 0.885 |
| release_gate_recall | 0.905 | 0.711 - 0.973 |
| duration_ms_mean | 1197.7 | 1117.9 - 1277.5 |
| duration_ms_p95 | 1906.2 | n/a |

## Confusion Matrix By Incident Family

| Family | True pass | True block | False block | False approve |
| --- | ---: | ---: | ---: | ---: |
| api_latency | 4 | 1 | 1 | 0 |
| auth_regression | 3 | 3 | 0 | 0 |
| cache_staleness | 5 | 1 | 0 | 0 |
| cost_spike | 4 | 2 | 0 | 0 |
| database_lock | 4 | 2 | 0 | 0 |
| deployment_rollback | 5 | 1 | 0 | 0 |
| misconfigured_quota | 4 | 1 | 1 | 0 |
| model_timeout | 6 | 0 | 0 | 0 |
| policy_violation | 5 | 0 | 0 | 1 |
| prompt_injection | 6 | 0 | 0 | 0 |
| queue_backlog | 3 | 2 | 1 | 0 |
| rate_limit | 4 | 1 | 0 | 1 |
| redis_restart | 4 | 2 | 0 | 0 |
| schema_drift | 4 | 1 | 1 | 0 |
| tool_loop | 3 | 1 | 2 | 0 |
| worker_termination | 5 | 1 | 0 | 0 |
