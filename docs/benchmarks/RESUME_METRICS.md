# AgentRail Resume Metrics

These numbers are generated from deterministic frozen synthetic scenarios, not tuned against the implementation after the fact. Each row links to its raw JSON artifact.

- Total frozen scenarios: `120`
- Deterministic seed: `agentrail-v1-frozen`
- Paid model-provider credentials required: `false`

| Benchmark | Scenarios | Task success | Tribunal consensus | False block | False approve | Gate precision | Gate recall | Raw artifact |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| smoke | 24 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.000 | [docs/benchmarks/artifacts/smoke-agentrail-v1-frozen.json](/docs/benchmarks/artifacts/smoke-agentrail-v1-frozen.json) |
| quality | 24 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.000 | [docs/benchmarks/artifacts/quality-agentrail-v1-frozen.json](/docs/benchmarks/artifacts/quality-agentrail-v1-frozen.json) |
| failures | 24 | 0.750 | 0.250 | 0.750 | 0.000 | 0.250 | 1.000 | [docs/benchmarks/artifacts/failures-agentrail-v1-frozen.json](/docs/benchmarks/artifacts/failures-agentrail-v1-frozen.json) |
| tribunal | 24 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.000 | [docs/benchmarks/artifacts/tribunal-agentrail-v1-frozen.json](/docs/benchmarks/artifacts/tribunal-agentrail-v1-frozen.json) |
| load | 24 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.000 | [docs/benchmarks/artifacts/load-agentrail-v1-frozen.json](/docs/benchmarks/artifacts/load-agentrail-v1-frozen.json) |

## Reproduction

```bash
make benchmark-report
```

The benchmark generator records scenario ids, benchmark seed, runtime metadata, confidence intervals, per-family confusion matrices, and raw per-scenario rows.
