# ADR NNNN — <the decision, stated as a claim rather than a topic>

- **Status:** Proposed | Accepted | Superseded by [ADR NNNN](NNNN-....md)
- **Date:** YYYY-MM-DD
- **Phase:** <build-plan phase this decision belongs to>

## Context

What forced a decision. State the constraints that were actually in tension — if nothing was in
tension, this probably does not need an ADR. Describe the situation as it was, in the past tense,
including what the code did before. A reader two years from now has no other record of it.

## Decision

What was decided, in the present tense and in the active voice. Be specific enough that someone can
tell whether the code still complies. Name the modules, settings or boundaries involved.

## Alternatives considered

The options that were genuinely on the table, each with the reason it lost. This is the section that
earns the ADR its keep: it prevents relitigating a settled question, and it is honest about the
options that were close calls rather than pretending the decision was obvious.

- **<Alternative>.** Rejected because ...
- **<Alternative>.** Deferred rather than rejected — revisit when ...

## Consequences

What this costs, not just what it buys. Include the parts a future maintainer would otherwise
discover by surprise: new dependencies, tables owned by something other than our migrations, type
suppressions, extra paths that must be kept in sync, operational burden.

---

## Notes on writing these

- One decision per ADR. If the title needs "and", it is likely two ADRs.
- ADRs are immutable once accepted. To change a decision, write a new ADR and mark the old one
  superseded, linking both ways. Do not edit history to look prescient.
- Link related ADRs by relative path so they resolve on GitHub and on disk.
- Number files sequentially; the number never changes even if the decision is later reversed.
