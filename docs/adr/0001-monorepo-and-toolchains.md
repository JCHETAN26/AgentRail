# ADR 0001 — Single monorepo with pnpm and uv workspaces

- **Status:** Accepted
- **Date:** 2026-07-26
- **Phase:** 0

## Context

AgentRail spans a React console, a Python API, a Python worker and a Python sandbox, all of which
share contracts and must be released together. Two questions needed answering before any code was
written: one repository or several, and how each language's dependencies are pinned.

The contract between the console and the API changes on almost every phase. Split repositories would
make a contract change a multi-repository, multi-PR operation with a window in which the two sides
disagree — precisely the class of drift this project exists to catch in _agents_.

## Decision

One repository, with two workspace managers:

- **pnpm workspace** for `apps/*` and `packages/*`. `pnpm-lock.yaml` is committed and CI installs
  with `--frozen-lockfile`.
- **uv workspace** rooted at `pyproject.toml`, with members `packages/core-py`, `services/api`,
  `services/worker` and `services/cloudops-sandbox`. `uv.lock` is committed and CI installs with
  `--frozen`.

Python tooling is shared from the root `pyproject.toml`: Ruff (format and lint), mypy in strict mode,
and pytest. Versions of Ruff, mypy and pytest are pinned exactly, so a tool upgrade is a reviewable
commit rather than a Tuesday morning surprise.

Every service is packaged from one `infra/compose/Dockerfile.python` parameterised by a
`SERVICE_PACKAGE` build argument.

## Alternatives considered

- **Separate repositories per service.** Rejected: contract drift, and no single `make verify`.
- **Nx or Turborepo.** Rejected for now: the build graph is four packages deep. Task orchestration is
  a `Makefile`, which is one fewer dependency and works identically in CI. Revisit if incremental
  builds become the bottleneck.
- **Poetry or pip-tools instead of uv.** Rejected: uv resolves and installs the whole workspace
  quickly enough that CI does not need a separate dependency cache warm-up step, and its workspace
  model matches the service layout directly.
- **A separate Dockerfile per service.** Rejected: three near-identical files drift, and a base image
  patch would need three edits.

## Consequences

- A single `make verify` covers every language, and CI mirrors it exactly.
- Cross-language changes (API contract plus console) land in one reviewable commit.
- Both lockfiles must be regenerated when dependencies change, and both are enforced in CI.
- The repository will grow large. If build times become a problem, the mitigation is task caching,
  not splitting the repository.
