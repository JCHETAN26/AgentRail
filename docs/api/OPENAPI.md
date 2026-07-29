# API documentation

The committed OpenAPI snapshot is generated from the FastAPI application and stored at
`packages/contracts/openapi.json`. The TypeScript client types in `packages/contracts/src/generated`
are generated from the same snapshot.

## Regenerate

```bash
make contracts
```

## Check drift

```bash
make contracts-check
```

CI fails when the FastAPI schema and committed contract snapshot diverge.

## Main resource groups

- Authentication and organisations.
- Agent definitions and immutable agent versions.
- Datasets, dataset versions and frozen evaluation suites.
- Evaluation runs, run items, trajectories and replay records.
- Evaluators, comparison reports, release policies and GitHub Check records.
- Safety Tribunal sessions, findings, arguments, verdicts and replays.
- Policy approvals, canary deployment records and observability metrics.

The OpenAPI file itself is the source of truth for request/response schema details.
