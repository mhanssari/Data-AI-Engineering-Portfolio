# 0005 Recompute every invoice line on every run, and keep a snapshot per run

Status: accepted

Date: 30 October 2026

## Context

The draft design said a late correction should recompute only the affected invoice lines. Building the matching engine showed that "affected" is hard to compute correctly. A corrected receipt changes cumulative quantities on sibling lines. A new invoice can turn into the duplicate of an old one. A supplier merge changes duplicate detection across the group. A new contract version or policy changes tolerances.

## Options

1. **Incremental: work out the affected set from changed keys.** Efficient at scale. Every dependency above must be enumerated, and a missed one silently leaves a wrong status on a financial control.
2. **Full recompute, overwrite in place.** Simple, but loses the previous answer, so "what changed?" cannot be answered.
3. **Full recompute, snapshot per run, current-state views.** Simple and auditable. Storage grows with runs.

## Decision and rationale

Option 3. At this volume the recompute is quick enough that the efficiency of option 1 buys nothing (the whole pipeline, both batches and the graph, takes about 12 seconds on running containers), and correctness of a financial control matters more. Consumers read `recon.current_*` and never see the run grain.

The requirement behind the original wording was that a late correction changes only the affected *results*. That is still true and is now easier to prove: the test compares two snapshots value for value and asserts that exactly the S17 and S29 lines changed and only the S20 and S31 lines were added.

## Consequences

- Simpler alternative for a smaller need: option 2, if nobody asks what a past run said.
- At scale, option 1 would become necessary. The right route would be a dependency-driven affected set, verified by periodically comparing it with a full recompute, and old snapshots would need retention rules. Neither is built.
- Snapshots record what the engine concluded in that run. They do not make GRNI bitemporal: a rerun of a past cut-off uses today's corrected documents.
