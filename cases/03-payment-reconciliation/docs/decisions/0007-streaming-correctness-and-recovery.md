# 0007 Durable intake, bounded live state and transactional recovery

Status: proposed engineering design; owner confirmed the metric and lateness choices below

Date: 12 November 2026

## Context and options

Batch acceptance A1–A8 is validated. Streaming must demonstrate duplicate handling, event time, restart and late recovery (A9–A12) while preserving SQL as the authority for matching, money and payment-state transitions. A watermark alone cannot provide a durable audit trail for records it excludes.

Options considered: one watermarked query as the only record of input; one stateless query with all state in PostgreSQL; or a durable intake query plus a separate bounded-state live query. Propose the third. It demonstrates Spark state eviction while retaining the original deliveries independently.

## Confirmed owner choices, 12 November 2026

- Authorisation decline rate is declined decisions divided by authorised plus declined decisions, with expiries shown separately. Definitions are owned by [the metric catalogue](../metrics.md).
- The live query uses a **10-minute event-time watermark delay**. Older events remain recoverable from durable intake through a controlled backfill. Spark watermark semantics do not guarantee that every event older than the threshold is excluded; tests must use observed watermark progress.

## Engineering proposal

- Shared Kafka follows [portfolio decision 0002](../../../../platform/decisions/0002-shared-kafka-topology.md). Workloads, topic specifications, dependencies, databases and checkpoints remain case-owned.
- **Intake query:** no watermark; persist every transport delivery with its payload/offset, canonical valid events and quarantine reasons. Capture the planted malformed record without preventing the rest from landing.
- **Live query:** strict schema/clock validation before the watermark, `dropDuplicatesWithinWatermark` by event ID, then a transactional PostgreSQL sink. Keep one Spark stateful operator; SQL derives minute summaries and latest payment state from the durable admitted-event ledger.
- Each query has its own checkpoint, generated consumer-group prefix and database batch ledger. Data writes and the corresponding `(query generation, role, batch ID)` commit record share one transaction. The sink also has event/transport uniqueness constraints; it never increments totals blindly.
- SQL applies events by source sequence, holds gaps and records illegal transitions, sharing the batch transition rules. Recompute the complete state of affected payments and replace affected minute totals. Do not use maximum event time or last arrival as payment-state authority.
- Intake and live queries commit independently. A published readiness/frontier indicator must show both queries' progress; the two outputs are not atomically consistent. Conflicting immutable event IDs or source-sequence collisions block trusted publication rather than being guessed away.
- Backfill pauses/fences the live writer, establishes a complete intake/reference frontier, adds valid missing events and rebuilds affected payment histories and minute totals atomically. It records the repair and resumes the same checkpoint. It must not reset the watermark, fabricate missing events or promote quarantined poison records.
- No end-to-end exactly-once claim. Describe replay-safe database effects under at-least-once delivery, subject to the implementation tests.

The [streaming design](../streaming-design.md) owns the contract, grains, runtime candidates and acceptance plan. No Kafka, Spark job, migration or dependency pin is implemented by this record.

## Sources and consequences

Spark's [streaming guide](https://spark.apache.org/docs/4.2.1/streaming/apis-on-dataframes-and-datasets.html) documents bounded deduplication, restrictions on chaining stateful operators, checkpoint recovery and the default at-least-once behaviour of `foreachBatch`. These support the design; they do not prove this proposed sink is correct. Two input readers and a durable database ledger cost more than a single query, which is acceptable for a bounded local fixture. Spark memory may be bounded while the durable audit history still grows.
