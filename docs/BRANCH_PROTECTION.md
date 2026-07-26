# Branch protection for `main`

> **Status: applied.** These settings are live on `main` as of 2026-07-26. This document records what
> is configured and why, so a future change is a deliberate edit rather than a rediscovery. Verify
> the current state with:
>
> ```bash
> gh api repos/JCHETAN26/AgentRail/branches/main/protection
> ```

## Applied settings

| Setting                                    | Value  | Why                                                                                   |
| ------------------------------------------ | ------ | ------------------------------------------------------------------------------------- |
| Require a pull request before merging      | ✅     | No direct pushes to `main`.                                                           |
| Required approvals                         | **0**  | See "Deviations" below — this is a one-maintainer repo.                               |
| Dismiss stale approvals on new commits     | ✅     | An approval applies to the code that was reviewed.                                    |
| Require approval of the most recent push   | ❌     | Meaningless at 0 required approvals.                                                  |
| Require code-owner review                  | ❌     | `CODEOWNERS` names the sole maintainer; requiring it would make every PR unmergeable. |
| Require conversation resolution            | ✅     | Review comments cannot be merged past.                                                |
| Require status checks to pass              | ✅     | See the list below.                                                                   |
| Require branches to be up to date (strict) | **❌** | See "Deviations".                                                                     |
| Require linear history                     | ✅     | Squash or rebase merges only.                                                         |
| Block force pushes                         | ✅     | History is append-only.                                                               |
| Restrict deletions                         | ✅     | `main` cannot be deleted.                                                             |
| Include administrators                     | ✅     | See "Deviations" — this one is load-bearing.                                          |
| Require signed commits                     | ❌     | Recommended later; needs local signing set up first.                                  |

## Required status checks

Configured by exact job name. Renaming a job in `.github/workflows/` breaks this configuration and
silently drops the requirement, so treat a rename as a breaking change.

```text
frontend
python
contracts
integration
e2e
build (agentrail-api)
build (agentrail-worker)
build (agentrail-cloudops-sandbox)
codeql (javascript-typescript)
codeql (python)
```

`dependency-review` is deliberately **not** required — see "Deviations".

Checks to add in later phases: `agent-quality / smoke-gate` (Phase 17), `containers / scan`
(Phase 14).

## Deviations from the build plan, and why

The build plan specifies one required approval and a strict up-to-date requirement. Three settings
depart from it. Each is a consequence of this being a single-maintainer repository, and each should
be revisited the moment a second maintainer joins.

**Required approvals is 0, not 1.** GitHub does not let an author approve their own pull request. On
a one-person repository, requiring an approval makes every pull request permanently unmergeable. The
PR-only guarantee is preserved — changes still cannot reach `main` except through a pull request with
green checks — but the review gate is currently the author's own discipline. **Set this to 1 as soon
as a second maintainer exists.**

**"Require branches to be up to date" (strict) is off.** With it on, every merge invalidates every
other open pull request, and each must be rebased before it can merge. With Dependabot opening
grouped updates weekly, that is a rebase treadmill for one person. The mitigation is that CI runs on
the merge result, not just the branch tip. Turn this on together with a merge queue, not before.

**`dependency-review` is not a required check.** It currently warns and skips because the dependency
graph is disabled. Requiring it before the graph is enabled would create a gate that cannot enforce
advisories or licences. **Add it to the required list immediately after enabling the dependency
graph** (below).

**"Include administrators" is on, and matters more than it looks.** With it off, an administrator's
`git push origin main` succeeds and GitHub merely logs `Bypassed rule violations`. That is not a
theoretical concern — it happened once while this configuration was being verified, which is why the
setting is now on and why the empty commit `565bc7c` exists in the history.

## Other repository settings

**Settings → General → Pull Requests**

- Allow squash merging — ✅ (default)
- Allow merge commits — ❌
- Allow rebase merging — optional
- Automatically delete head branches — ✅

**Settings → Code security and analysis**

- **Dependency graph — ❌ STILL OFF. This is the one outstanding item, and it can only be done in the
  browser.** GitHub exposes no REST field for it: `PATCH /repos/{owner}/{repo}` accepts
  `security_and_analysis.secret_scanning*` and `dependabot_security_updates`, but not
  `dependency_graph`. Confirm the current state with
  `gh api repos/JCHETAN26/AgentRail/dependency-graph/sbom` — a `404` means it is off.

  Until it is enabled, `dependency-review` warns and skips, and `dependabot_security_updates` cannot
  be turned on either, because it depends on the graph. Enable it at
  [`settings/security_analysis`](https://github.com/JCHETAN26/AgentRail/settings/security_analysis),
  then add `dependency-review` to the required-checks list above.

- Dependabot version updates — ✅ (driven by `.github/dependabot.yml`; works without the graph)
- Dependabot alerts / security updates — blocked on the dependency graph
- Secret scanning — ✅ enabled
- Secret scanning push protection — ✅ enabled
- Private vulnerability reporting — ✅ (advisory link in `.github/ISSUE_TEMPLATE/config.yml`)

**Settings → Actions → General** — verified already correct:

- `default_workflow_permissions: read` — each workflow requests more explicitly where it needs it.
- `can_approve_pull_request_reviews: false` — Actions cannot approve pull requests.

## Environments (from Phase 16)

Not needed yet. When deployment lands, create `staging` and `production` environments, require a
reviewer on `production`, restrict it to the `main` branch, and use GitHub OIDC rather than
long-lived cloud credentials.

## Verifying

Verified on 2026-07-26:

1. **A direct push to `main` is rejected.**

   ```text
   remote: error: GH006: Protected branch update failed for refs/heads/main.
   ! [remote rejected] main -> main (protected branch hook declined)
   ```

   Do not accept a `Bypassed rule violations` message as a pass — that means the push _succeeded_
   and "Include administrators" is off.

2. A pull request cannot be merged while a required check is failing. `dependency-review` currently
   skips while the dependency graph is off and is deliberately not in the required list.

3. Approval dismissal is not observable at 0 required approvals. Re-verify when the requirement is
   raised to 1.
