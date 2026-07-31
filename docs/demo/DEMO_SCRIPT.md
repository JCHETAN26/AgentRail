# Five-minute demo script

## 0:00 to 0:30

Open AgentRail and state the product in one sentence:

> AgentRail is a release platform for AI agents: it proves an agent change is safer before users see
> it.

Point to the recorded-mode banner and explain that the demo uses deterministic evidence and no paid
model keys.

## 0:30 to 1:30

Show the workspace:

- Organisation and project context.
- Latest evaluation run.
- Pass/fail, cost, latency and policy summary.
- Correlation id visible for debugging.

## 1:30 to 2:30

Open a failed item:

- Show the trajectory timeline.
- Highlight the failing step and redacted evidence.
- Explain that retries and duplicate queue delivery are safe because PostgreSQL owns state.

## 2:30 to 3:30

Open the Safety Tribunal:

- Prosecutor and Auditor findings.
- Defender rebuttal.
- Judge verdict and dissent.
- Explain Auditor blocker override and prompt-version provenance.

## 3:30 to 4:20

Show release evidence:

- Release gate threshold.
- GitHub Check record.
- Canary decision and rollback reason.
- Benchmark summary from `docs/benchmarks/RESUME_METRICS.md`.

## 4:20 to 5:00

Close with the engineer path:

```bash
uv run ruff format --check services packages && pnpm run format:check
uv run ruff check services packages && pnpm run lint
uv run mypy packages/core-py/src services/*/src && pnpm run typecheck
AGENTRAIL_REQUIRE_INTEGRATION=1 uv run pytest -q && pnpm run test
uv run python scripts/export_openapi.py && pnpm --filter @agentrail/contracts check
uv run python scripts/benchmark.py report
```

These are what `make verify` and `make benchmark-report` run; use the make
targets if GNU make is available. Spelled out because make is not present on
every machine, and because a shortened list would claim more verification than
it performs. `AGENTRAIL_REQUIRE_INTEGRATION=1` matters: without it the
integration tests _skip_ when PostgreSQL or Redis is absent, and a skipped suite
reads as a passing one.

Then show the raw artifact link for one benchmark metric.
