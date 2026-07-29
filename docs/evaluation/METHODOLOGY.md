# Evaluation methodology

AgentRail treats an agent release as a software release candidate. A candidate must prove itself on
frozen evidence before it can reach users.

## Dataset rules

- Uploads are JSONL or CSV and must pass schema validation.
- Dataset versions are immutable and content-addressed.
- Evaluation suites freeze the dataset version, evaluator set, thresholds, fault profile and Tribunal
  settings.
- Frozen benchmark scenarios are never tuned against after results are known.

## Evaluator families

- **Outcome:** final task success.
- **Diagnosis:** root cause and reasoning quality.
- **Tool selection:** required tools used, forbidden tools avoided.
- **Tool arguments:** schema validity and semantic correctness.
- **Evidence support:** rationale grounded in retrieved or recorded evidence.
- **Policy compliance:** no prohibited action, escalation or approval bypass.
- **Side effects:** idempotent, approved and reversible where required.
- **Budgets:** latency, token and cost limits.

Errors remain in denominators. A failed executor, missing trajectory or evaluator error cannot make a
run look better by disappearing from the score.

## Tribunal quality metrics

The Safety Tribunal is measured separately from programmatic evaluators:

- Verdict agreement with programmatic gate.
- False-block rate.
- False-approve rate.
- Consensus rate.
- Duration overhead.

The deterministic benchmark runner records confidence intervals and per-family confusion matrices so
each number can be traced to raw scenario rows.

## Reproduce

```bash
make benchmark-report
```

Generated evidence is stored in `docs/benchmarks/artifacts/` and summarized in
`docs/benchmarks/RESUME_METRICS.md`.
