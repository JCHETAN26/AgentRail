# Test strategy

`make verify` is the deterministic contract: formatting, lint, strict type checks, unit tests and
contract drift checks. It needs no model-provider credentials and no running services.

## Test layers

| Layer                          | Command                              | Purpose                                                                              |
| ------------------------------ | ------------------------------------ | ------------------------------------------------------------------------------------ |
| Python unit and property tests | `uv run pytest -m "not integration"` | Domain rules, state machines, policies, evaluators, Tribunal, replay and benchmarks. |
| TypeScript unit tests          | `pnpm run test`                      | Console components, API client behavior and user workflows.                          |
| Contract checks                | `make contracts-check`               | FastAPI OpenAPI snapshot and generated TypeScript client are current.                |
| Integration tests              | `make integration`                   | PostgreSQL, Redis, migrations, worker leases and cross-tenant isolation.             |
| E2E tests                      | `make e2e`                           | Browser-visible job, approval and Tribunal flows against a running stack.            |
| Container checks               | CI `containers / scan`               | Build images, assert non-root runtime, scan vulnerabilities, emit SBOM/provenance.   |
| Benchmark reproducibility      | `make benchmark-report`              | Frozen scenario metrics, confidence intervals and raw artifacts.                     |

## Invariants

- Duplicate delivery does not duplicate side effects.
- Terminal states do not transition.
- Tenant A cannot observe tenant B data.
- Frozen suites and immutable versions cannot be mutated.
- Errors remain in evaluator denominators.
- Auditor blockers override Tribunal approval.
- Missing live-model credentials fail closed.
- Release gates block on failed policy, failed threshold or blocking Tribunal verdict.

## Local resource guidance

Do not run Docker locally on constrained machines unless integration or E2E verification requires it.
Use GitHub Actions or Codespaces for full-stack runs, then stop the Codespace when done.
