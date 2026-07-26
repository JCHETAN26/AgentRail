# Branch protection for `main`

These settings are **not applied automatically** — repository configuration is changed only by the
repository owner. Apply them once, after the Phase 0 pull request is merged.

## Required settings

**Settings → Branches → Add branch ruleset** (or classic branch protection) targeting `main`:

| Setting                                     | Value                     | Why                                                   |
| ------------------------------------------- | ------------------------- | ----------------------------------------------------- |
| Require a pull request before merging       | ✅                        | No direct pushes to `main`.                           |
| Required approvals                          | 1                         | Nothing merges unreviewed.                            |
| Dismiss stale approvals on new commits      | ✅                        | An approval applies to the code that was reviewed.    |
| Require approval of the most recent push    | ✅                        | Prevents self-approving a change pushed after review. |
| Require conversation resolution             | ✅                        | Review comments cannot be merged past.                |
| Require status checks to pass               | ✅                        | See the list below.                                   |
| Require branches to be up to date           | ✅ (or use a merge queue) | Prevents semantic conflicts that CI never saw.        |
| Require linear history                      | ✅                        | Squash or rebase merges only.                         |
| Block force pushes                          | ✅                        | History is append-only.                               |
| Restrict deletions                          | ✅                        | `main` cannot be deleted.                             |
| Include administrators / apply to bypassers | ✅                        | The rules apply to everyone.                          |
| Require signed commits                      | Recommended               | Optional but preferred.                               |

## Required status checks

Add these by name. They are stable job names; renaming one in `.github/workflows/` is a breaking
change to this configuration.

```text
ci / frontend
ci / python
ci / contracts
ci / integration
ci / e2e
ci / build            (matrix: agentrail-api, agentrail-worker, agentrail-cloudops-sandbox)
codeql / codeql       (matrix: javascript-typescript, python)
dependency-review / dependency-review
```

> A status check can only be _selected_ in the UI after it has run at least once on the repository.
> Open the Phase 0 pull request first, let CI run, then add the checks.

Checks added in later phases: `agent-quality / smoke-gate` (Phase 17), `containers / scan` (Phase 14).

## Other repository settings

**Settings → General → Pull Requests**

- Allow squash merging — ✅ (default)
- Allow merge commits — ❌
- Allow rebase merging — optional
- Automatically delete head branches — ✅

**Settings → Code security and analysis**

- Dependency graph — ✅ **Required before `dependency-review` can pass.** Until it is enabled the
  workflow fails with _"Dependency review is not supported on this repository."_ Enable it at
  [`settings/security_analysis`](https://github.com/JCHETAN26/AgentRail/settings/security_analysis),
  then re-run the check.
- Dependabot alerts — ✅
- Dependabot security updates — ✅
- Secret scanning — ✅
- Secret scanning push protection — ✅
- Private vulnerability reporting — ✅

**Settings → Actions → General**

- Workflow permissions: **Read repository contents permission** (each workflow requests more
  explicitly where it needs it).
- Allow GitHub Actions to create and approve pull requests — ❌

## Environments (from Phase 16)

Not needed yet. When deployment lands, create `staging` and `production` environments, require a
reviewer on `production`, restrict it to the `main` branch, and use GitHub OIDC rather than
long-lived cloud credentials.

## Verifying

After applying, confirm that:

1. `git push origin main` from a clone is rejected.
2. A pull request cannot be merged while any required check is failing.
3. A new push to a branch dismisses the existing approval.
