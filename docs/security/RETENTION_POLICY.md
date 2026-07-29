# Retention policy

AgentRail keeps evidence long enough to audit a release, then prunes data that can expose sensitive
payloads or create unnecessary storage cost.

## Defaults

| Data class                       | Default retention | Notes                                                              |
| -------------------------------- | ----------------: | ------------------------------------------------------------------ |
| API sessions                     |           30 days | Revoked immediately on sign-out.                                   |
| API key usage records            |          180 days | Keys are stored only as one-way hashes.                            |
| Audit logs                       |          365 days | Policy, approval, Tribunal and release decisions are audit events. |
| Evaluation runs and summaries    |          365 days | Keep release evidence and SLO verdicts.                            |
| Trajectory steps and checkpoints |           90 days | Redacted before persistence; shorter than summaries.               |
| Dataset validation reports       |          365 days | Needed to reproduce suite provenance.                              |
| Benchmark artifacts              |         Immutable | Frozen evidence is content-addressed and never tuned against.      |

## Controls

- Sensitive keys and email addresses are redacted before logs or trajectories are persisted.
- Cross-tenant reads are denied before response shaping.
- PostgreSQL RLS provides defence in depth for tenant-owned tables.
- Quota ledgers are durable, but short-lived rate-limit counters live in Redis only.

## Operational expectation

Run retention pruning from a scheduled worker or deployment job. Failed pruning should alert, but it
must never delete data from another tenant or hide release evidence for active incidents.
