# 0006 Incremental ingestion: append-only raw, watermark with lookback, upsert if newer

Status: accepted

Date: 30 October 2026

## Context

Two ERP-shaped sources deliver a full extract and then change logs (simulated CDC: rows with an operation and a source change time). Deliveries can repeat, arrive out of order, arrive late or reference master data not yet delivered. Reruns must be safe.

## Options

1. **Full reload each time.** Simplest and needs no state, but does not show or support the incremental pattern, and it cannot express deletes without comparing extracts.
2. **Trust the change log in order.** Simple until a row arrives late or twice.
3. **Append-only raw, then derive core from the latest version per key, with a watermark as an optimisation.** More moving parts; tolerant of all the above.

## Decision and rationale

Option 3. Each failure mode has one specific defence:

| Failure | Defence |
| --- | --- |
| The same file delivered again | Checksum in `ops.ingest_file`: skipped |
| Truncated or corrupt delivery | Manifest row count and checksum; the batch rolls back |
| Schema drift | Header compared with the raw table before landing |
| Row delivered twice, or out of order | Latest version by source change time, and upsert only if newer |
| Row stamped before the watermark | 24-hour lookback |
| Parent not delivered yet | Quarantine with a reason, retried every run; parents first, so a chain clears in one run |
| Crash mid-run | Each step is one transaction. The run row is on its own connection, so the failure is still recorded |
| Row older than the lookback | Missed by design, and the row-count reconciliation reports it |

The last row matters most. A watermark is an optimisation with a known hole, so the reconciliation compares every current source key with core and does not rely on the watermark at all.

## Consequences

- The simulated CDC is files. A real source would be log-based, and ordering and delete semantics would need revisiting.
- The stg views scan all raw rows, because small data allows it. At scale, raw needs partitioning and the window must be pushed down.
- The 24-hour lookback is a judgement, not a measured property of these sources; a real value would come from observed lateness.
- Foreign keys in `core` are real, so quarantine is what keeps conform from failing on a late parent, and a bug in it fails loudly.

Verified by tests: redelivery lands nothing, conform writes nothing on rerun, stale and duplicate rows have no effect, S29 needs the lookback (removing it fails the suite), and the deliberately missed row fails the reconciliation.
