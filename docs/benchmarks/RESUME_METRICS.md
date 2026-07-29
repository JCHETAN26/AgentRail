# AgentRail Resume Metrics

These numbers are generated from deterministic frozen synthetic scenarios, not tuned against the implementation after the fact. Each row links to its raw JSON artifact.

- Total frozen scenarios: `736`
- Deterministic seed: `agentrail-v1-frozen`
- Paid model-provider credentials required: `false`

| Benchmark | Scenarios | Task success | Tribunal consensus | False block | False approve | Gate precision | Gate recall | Raw artifact                                                                                                                |
| --------- | --------: | -----------: | -----------------: | ----------: | ------------: | -------------: | ----------: | --------------------------------------------------------------------------------------------------------------------------- |
| smoke     |        32 |        0.938 |              0.875 |       0.094 |         0.031 |          0.571 |       0.800 | [docs/benchmarks/artifacts/smoke-agentrail-v1-frozen.json](/docs/benchmarks/artifacts/smoke-agentrail-v1-frozen.json)       |
| quality   |       320 |        0.903 |              0.922 |       0.069 |         0.009 |          0.703 |       0.945 | [docs/benchmarks/artifacts/quality-agentrail-v1-frozen.json](/docs/benchmarks/artifacts/quality-agentrail-v1-frozen.json)   |
| failures  |       160 |        0.794 |              0.906 |       0.075 |         0.019 |          0.760 |       0.927 | [docs/benchmarks/artifacts/failures-agentrail-v1-frozen.json](/docs/benchmarks/artifacts/failures-agentrail-v1-frozen.json) |
| tribunal  |       128 |        0.859 |              0.977 |       0.023 |         0.000 |          0.903 |       1.000 | [docs/benchmarks/artifacts/tribunal-agentrail-v1-frozen.json](/docs/benchmarks/artifacts/tribunal-agentrail-v1-frozen.json) |
| load      |        96 |        0.854 |              0.917 |       0.062 |         0.021 |          0.760 |       0.905 | [docs/benchmarks/artifacts/load-agentrail-v1-frozen.json](/docs/benchmarks/artifacts/load-agentrail-v1-frozen.json)         |

## Reproduction

```bash
make benchmark-report
```

The benchmark generator records scenario ids, benchmark seed, runtime metadata, confidence intervals, per-family confusion matrices, and raw per-scenario rows.
