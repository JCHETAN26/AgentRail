# Tribunal design and prompt versioning

The Safety Tribunal is a six-role debate over release evidence:

- Prosecutor: searches for failures and adversarial gaps.
- Defender: contextualizes failures and argues for safe release when evidence supports it.
- Auditor: checks policy and compliance; blocker findings override approval.
- Economist: evaluates latency, tokens and cost.
- Historian: compares candidate evidence against baseline and prior releases.
- Judge: synthesizes a structured verdict.

## Rounds

1. Evidence: Prosecutor, Auditor, Economist and Historian write findings to the blackboard.
2. Debate: Defender rebuts findings; Prosecutor may counter once.
3. Verdict: Judge reads the blackboard and emits `approved`, `conditional` or `blocked`.
4. Gate: release policy consumes the verdict when Tribunal approval is required.

## Prompt version contract

Each role prompt is immutable and content-addressed:

- Role name.
- Prompt version id.
- System prompt digest.
- Response schema digest.
- Model provider and model name.
- Source commit.

Prompt text is not assembled from user evidence. Evidence enters only as sandboxed content, and model
output must validate against the role schema before it can become a finding, argument or verdict.

## Recorded and live modes

Recorded mode is deterministic and safe for CI, demos and public benchmark reproduction. Live mode
uses the same persisted blackboard and prompt metadata, but routes role calls through a model client.
Missing live credentials fail closed; the system does not silently replace live debate with recorded
responses.

## Replay

Recorded Tribunal replay should reproduce the original digest. Forked replay may override a role
prompt, model or Defender strategy and persists a new digest plus divergence summary without mutating
the original verdict.
