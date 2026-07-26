# Contributing to AgentRail

## The one absolute rule

**Everything reaches `main` through a pull request.** No direct commits, no direct pushes, no
force-pushes to a shared branch, no merging without review, no bypassing a required check, and never
weakening a test to make CI green.

## Workflow

```bash
git checkout main
git pull --ff-only origin main
git checkout -b feat/pXX-short-description

# ... work ...

make verify          # must pass before you push
git push -u origin feat/pXX-short-description
gh pr create --draft --fill
```

Mark the pull request ready for review only when every required check is green. Fill in the template
honestly — in particular, do not claim a command passed unless you ran it.

## Branch and commit naming

Branches: `feat/pXX-short-description`, `fix/short-description`, `docs/short-description`,
`chore/short-description`.

Commits follow [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat(api): add cursor pagination to the jobs list
fix(worker): drop messages for jobs that no longer exist
docs(adr): record the contract generation decision
chore(deps): bump ruff to 0.9.2
```

Scopes in use: `api`, `worker`, `sandbox`, `web`, `core`, `contracts`, `infra`, `ci`, `docs`, `deps`.

## Phase discipline

AgentRail is built in the phases listed in the build plan, one pull request per phase. Do not build
future-phase infrastructure ahead of time — a speculative abstraction with no caller is harder to
review than the thing it was meant to prevent.

Update `docs/CHECKPOINT.md` in the same pull request that completes a phase.

## Code standards

### TypeScript

- Strict mode, including `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`.
- No `any`. An unavoidable exception needs a comment explaining why.
- Validate anything crossing a boundary; never trust a network response's shape.
- Implement loading, empty, error and permission-denied states — not just the happy path.
- Test user-visible behaviour, not implementation details. No snapshot tests of whole components.
- Never `dangerouslySetInnerHTML` with content the platform did not author.

### Python

- Strict mypy on every `src` tree. Public interfaces are typed.
- Pydantic at every boundary; the domain does not accept unvalidated input.
- Business logic does not live in route handlers.
- Evaluators and domain code must not import FastAPI, LangGraph or a provider SDK.
- Pass explicit timeouts. An unbounded network call is a bug.

### Both

- A comment should explain _why_, not restate the code.
- Prefer one clear implementation to a configurable abstraction with one caller.

## Tests

A change is incomplete without tests appropriate to its risk.

| Kind        | Where                                  | Runs against                           |
| ----------- | -------------------------------------- | -------------------------------------- |
| Unit        | `packages/*/tests`, `services/*/tests` | Nothing external                       |
| Integration | marked `@pytest.mark.integration`      | Real PostgreSQL and Redis              |
| Component   | `apps/web/tests`                       | jsdom, mocked `fetch`                  |
| End-to-end  | `apps/web/e2e`                         | A real browser against a running stack |

Do not mock the thing you are trying to prove. Integration tests use real dependencies and the real
migrations. A process-start check is not an end-to-end test.

## Security

Read `docs/security/THREAT_MODEL.md` before changing a boundary, and update it in the same pull
request if the boundary moves.

Never commit a secret, put a real credential in `.env.example`, log a raw secret or prompt, return a
stack trace to a client, or describe synthetic sandbox output as real telemetry.

Report vulnerabilities privately — see `SECURITY.md`. Do not open a public issue.

## Contracts

Changing a request or response shape means running:

```bash
make contracts
```

CI regenerates both artefacts and fails if the committed copies differ.

## Documentation

Documentation lands in the same pull request as the change it describes. Add an ADR when you make a
decision that a future maintainer would otherwise have to reverse-engineer: state the problem, the
alternatives, the trade-offs and the consequences.

## Getting set up

See `docs/operations/LOCAL_DEVELOPMENT.md`.
