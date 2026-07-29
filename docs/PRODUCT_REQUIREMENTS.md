# Product requirements

## Problem

Teams can ship an agent prompt, graph, tool list or model change faster than they can prove it is
safe. Existing eval tools usually stop at pass/fail scores, while production failures are about
trajectory quality, policy violations, retries, side effects, cost, approval gates and rollback.

## Target users

- AI platform engineers maintaining agent releases.
- Security and compliance reviewers who need auditable evidence.
- Engineering managers deciding whether a candidate agent version can ship.

## Product promise

AgentRail turns an agent version into a release candidate with evidence: frozen datasets, reproducible
execution, redacted traces, policy gates, replay, failure injection, a multi-agent Safety Tribunal,
GitHub Checks, canary records and rollback history.

## Must-have workflows

1. Register an immutable agent version.
2. Upload and freeze a dataset/suite.
3. Run deterministic or model-backed evaluations.
4. Inspect failed trajectories and replay from checkpoints.
5. Route high-risk actions through policy and approval gates.
6. Run the Safety Tribunal over evidence and bind verdicts to release policy.
7. Publish PR feedback through GitHub Checks.
8. Promote through canary and preserve rollback reasons.

## Non-goals for v1

- Real customer infrastructure access from the CloudOps sandbox.
- Unbounded public demo usage.
- Silent fallback from live model mode to recorded mode.
- Production claims without frozen benchmark artifacts.

## Success metrics

- An engineer can reproduce the benchmark with `make benchmark-report`.
- A reviewer can jump from a failed release gate to the exact trace evidence.
- A demo user can understand the value in under 30 seconds without paid API keys.
- A release can be blocked by policy, Tribunal verdict or canary regression before user exposure.
