# ADR 0005 — The API generates the contract; a committed snapshot makes drift a CI failure

- **Status:** Accepted
- **Date:** 2026-07-26
- **Phase:** 0

## Context

The console and the API must agree on request and response shapes, and that agreement has to survive
eighteen phases of change. Hand-written TypeScript interfaces mirroring Pydantic models drift the
first time somebody adds an optional field — and drift silently, because both sides still compile.

## Decision

The FastAPI application is the single source of truth. Two generated artefacts are committed:

1. `packages/contracts/openapi.json` — produced by `scripts/export_openapi.py` from
   `create_app().openapi()`, serialised with sorted keys and stable indentation.
2. `packages/contracts/src/generated/api.ts` — produced from that snapshot by `openapi-typescript`.

`make contracts` regenerates both. The `ci / contracts` job regenerates them into a scratch location
and fails if either differs from what is committed. A contract change that the author did not
regenerate cannot merge.

The web client (`apps/web/src/lib/api.ts`) imports its types from `@agentrail/contracts` and never
declares its own copy of an API shape.

Both generated files are listed in `.prettierignore`. Reformatting them would break the byte-for-byte
comparison the drift check depends on.

## Why commit generated files

The usual objection is that generated artefacts do not belong in version control. Here they earn
their place:

- The diff of `openapi.json` in a pull request _is_ the contract change, reviewable directly.
- The console can be type-checked without booting Python.
- CI has an artefact to compare against; without one, "regenerate and diff" has nothing to diff.

Operation ids are set from route function names (`_use_route_names_as_operation_ids`) so the generated
client has stable, readable method names instead of FastAPI's default path-derived ones.

## Alternatives considered

- **Generate at build time, commit nothing.** Rejected: no reviewable contract diff, and the console's
  type check would depend on a Python process.
- **Hand-written shared types.** Rejected: drifts silently, which is the failure this ADR exists to
  prevent.
- **A schema-first workflow (write OpenAPI, generate both sides).** Defensible, and common in
  multi-team organisations. Rejected here because FastAPI already derives the document from the
  Pydantic models that validate the requests, so the runtime and the document cannot disagree.
- **Generating a full client (methods, not just types).** Deferred. Phase 0 has three endpoints; a
  thin hand-written fetch wrapper over generated _types_ is clearer and lets the client own error
  translation into `ApiError`. Revisit when the surface is large enough that the wrapper is mostly
  boilerplate.

## Consequences

- Changing an endpoint requires running `make contracts`; forgetting is caught by CI, not by a
  reviewer.
- `packages/contracts/tests/contracts.test.ts` additionally asserts properties of the document that a
  generator cannot check — that every operation has a unique `operationId`, that error responses
  document `ProblemDetail`, and that the `JobState` enum matches the terminal-state helper.
- Two generation steps must stay in sync with their pinned tool versions (`openapi-typescript` is
  pinned exactly).
- If a future phase adds a second consumer language, it generates from the same snapshot.
