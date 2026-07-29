# AgentRail Quality Benchmark

- Seed: `agentrail-v1-frozen`
- Scenario count: `320`
- Raw artifact: `docs/benchmarks/artifacts/quality-agentrail-v1-frozen.json`
- Generated at: `2026-07-29T02:58:54+00:00`
- No frozen-test tuning: `true`

| Metric | Value | 95% CI |
| --- | ---: | ---: |
| task_success | 0.903 | 0.866 - 0.931 |
| programmatic_pass | 0.828 | 0.783 - 0.866 |
| tribunal_approval | 0.769 | 0.720 - 0.812 |
| tribunal_consensus | 0.922 | 0.887 - 0.947 |
| false_block | 0.069 | 0.046 - 0.102 |
| false_approve | 0.009 | 0.003 - 0.027 |
| release_gate_precision | 0.703 | 0.591 - 0.795 |
| release_gate_recall | 0.945 | 0.851 - 0.981 |
| duration_ms_mean | 1833.8 | 1804.9 - 1862.7 |
| duration_ms_p95 | 2241.1 | n/a |

## Confusion Matrix By Incident Family

| Family | True pass | True block | False block | False approve |
| --- | ---: | ---: | ---: | ---: |
| api_latency | 18 | 2 | 0 | 0 |
| auth_regression | 14 | 4 | 2 | 0 |
| cache_staleness | 15 | 4 | 1 | 0 |
| cost_spike | 16 | 3 | 0 | 1 |
| database_lock | 15 | 3 | 2 | 0 |
| deployment_rollback | 17 | 2 | 1 | 0 |
| misconfigured_quota | 14 | 2 | 4 | 0 |
| model_timeout | 16 | 3 | 1 | 0 |
| policy_violation | 17 | 1 | 1 | 1 |
| prompt_injection | 12 | 6 | 2 | 0 |
| queue_backlog | 14 | 5 | 1 | 0 |
| rate_limit | 17 | 3 | 0 | 0 |
| redis_restart | 10 | 8 | 1 | 1 |
| schema_drift | 15 | 1 | 4 | 0 |
| tool_loop | 17 | 3 | 0 | 0 |
| worker_termination | 16 | 2 | 2 | 0 |
