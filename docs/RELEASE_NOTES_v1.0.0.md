# AgentRail v1.0.0 release notes

## Highlights

- Immutable agent registry, dataset versions and frozen evaluation suites.
- Durable evaluation execution with PostgreSQL-owned state, Redis delivery and idempotent side
  effects.
- Redacted trajectories, checkpoints and replay records.
- Programmatic evaluators, comparison reports and release gates.
- Multi-agent Safety Tribunal with deterministic and live-model scaffolding.
- Policy approvals for high-risk actions.
- Canary deployment records and rollback reason preservation.
- Container hardening, SBOM/provenance artifacts and vulnerability scanning.
- Deterministic benchmark runner with frozen scenario artifacts and confidence intervals.
- Helm, GHCR and AWS OIDC deployment scaffolding.

## Verification

- `uv run pytest packages/core-py/tests/test_benchmarks.py`
- `uv run ruff check packages/core-py/src/agentrail_core/benchmarks.py packages/core-py/tests/test_benchmarks.py scripts/benchmark.py`
- `uv run mypy packages/core-py/src/agentrail_core/benchmarks.py scripts/benchmark.py`
- `uv run python scripts/check_github_actions_pinned.py`
- `uv run python scripts/benchmark.py report`

## Known gaps

- A real public deployment URL still requires cloud account wiring and smoke verification.
- GitHub App installation and live PR annotations are scaffolded but not fully exercised.
- OpenTelemetry export, dashboards and alert rules are not production-hosted yet.
- The public demo video has not been recorded.
