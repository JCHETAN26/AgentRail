# AgentRail Failures Benchmark

- Seed: `agentrail-v1-frozen`
- Scenario count: `160`
- Raw artifact: `docs/benchmarks/artifacts/failures-agentrail-v1-frozen.json`
- Generated at: `2026-07-29T02:58:54+00:00`
- No frozen-test tuning: `true`

| Metric | Value | 95% CI |
| --- | ---: | ---: |
| task_success | 0.794 | 0.725 - 0.849 |
| programmatic_pass | 0.744 | 0.671 - 0.805 |
| tribunal_approval | 0.688 | 0.612 - 0.754 |
| tribunal_consensus | 0.906 | 0.851 - 0.942 |
| false_block | 0.075 | 0.043 - 0.127 |
| false_approve | 0.019 | 0.006 - 0.054 |
| release_gate_precision | 0.760 | 0.626 - 0.857 |
| release_gate_recall | 0.927 | 0.806 - 0.975 |
| duration_ms_mean | 1598.9 | 1533.3 - 1664.5 |
| duration_ms_p95 | 2401.7 | n/a |

## Confusion Matrix By Incident Family

| Family | True pass | True block | False block | False approve |
| --- | ---: | ---: | ---: | ---: |
| api_latency | 8 | 1 | 1 | 0 |
| auth_regression | 7 | 3 | 0 | 0 |
| cache_staleness | 8 | 1 | 1 | 0 |
| cost_spike | 6 | 4 | 0 | 0 |
| database_lock | 3 | 3 | 3 | 1 |
| deployment_rollback | 6 | 4 | 0 | 0 |
| misconfigured_quota | 7 | 3 | 0 | 0 |
| model_timeout | 6 | 2 | 1 | 1 |
| policy_violation | 8 | 2 | 0 | 0 |
| prompt_injection | 9 | 0 | 1 | 0 |
| queue_backlog | 8 | 2 | 0 | 0 |
| rate_limit | 6 | 4 | 0 | 0 |
| redis_restart | 5 | 4 | 1 | 0 |
| schema_drift | 6 | 2 | 2 | 0 |
| tool_loop | 7 | 1 | 1 | 1 |
| worker_termination | 7 | 2 | 1 | 0 |
