# AgentRail Build Pack

This package contains the complete plan and Claude Code instructions for building AgentRail as a polished, deployed, benchmarked full-stack AI engineering project.

## Files

- `BUILDPLAN.md` — product scope, architecture, security, testing, benchmarking, CI/CD, deployment, demo design, and Phases 0–18.
- `SYSTEM_PROMPT.md` — persistent Claude Code engineering and PR-only rules.
- `CLAUDE_CODE_MASTER_PROMPT.md` — the prompt to start or resume each phase.

## Usage

1. Create the AgentRail repository.
2. Add these files to the initial repository.
3. Configure `SYSTEM_PROMPT.md` as Claude Code’s persistent project instructions.
4. Paste `CLAUDE_CODE_MASTER_PROMPT.md`.
5. Claude creates a Phase 0 feature branch, implements the phase, opens a PR, and stops.
6. Review and merge the PR.
7. Paste the same master prompt for the next phase.

The master prompt deliberately completes one phase per pull request. This keeps the architecture reviewable and prevents an uncontrolled all-at-once build.

After Phase 0, configure the `main` branch protection settings documented in `docs/BRANCH_PROTECTION.md`.
