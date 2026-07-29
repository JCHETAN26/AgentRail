# Technical deep dive outline

## Title

Building a release gate for AI agents, not just an eval dashboard

## Hook

Agent failures rarely look like one bad final answer. They look like wrong tools, missing approval,
silent retries, ungrounded evidence, cost spikes and side effects that are hard to replay.

## Sections

1. Why agent releases need software-release discipline.
2. Immutable agent versions, frozen suites and reproducible datasets.
3. PostgreSQL as the source of truth; Redis as delivery only.
4. Redacted trajectory capture and checkpoint replay.
5. Programmatic evaluators with errors kept in denominators.
6. The multi-agent Safety Tribunal as adversarial review over evidence.
7. Policy approvals and release gates.
8. Canary simulation and rollback reason preservation.
9. Benchmark methodology and confidence intervals.
10. What remains before production hardening.

## Evidence to include

- Architecture diagram from README.
- A failed trajectory screenshot.
- Tribunal blackboard screenshot.
- `docs/benchmarks/RESUME_METRICS.md` table.
- A raw benchmark JSON artifact.
- CI status and container scan summary.
