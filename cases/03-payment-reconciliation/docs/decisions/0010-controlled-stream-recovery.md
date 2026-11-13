# 0010 Abrupt consumer recovery and bounded late backfill

Status: accepted for the local recovery slice

Date: 12 November 2026

The [live design](0009-live-state-and-operational-projections.md) preserves intake separately from events admitted by Spark. The next requirement is to demonstrate recovery across the database/checkpoint boundary, then repair an event actually excluded by the watermark without resetting that checkpoint.

## Consumer failures

An opt-in diagnostic hook exits the Python consumer abruptly inside the live sink transaction after writing business rows and the batch record, or immediately after PostgreSQL commits and before the callback returns. It bypasses exception cleanup and Spark stop. Docker stops the consumer's child JVM when PID 1 exits. Exit codes 86/87, callback batch ID, the PostgreSQL batch record and Spark offset/commit files distinguish the two boundaries. The hook accepts only disposable `payments_stream_check_*` databases, `case03.live-check-*` topics and the live role; it is absent from normal execution.

Before-commit failure must roll back the ledger, projections and batch record. After-commit failure must leave the PostgreSQL transaction committed but the Spark batch uncommitted. Restart uses the same query/generation/checkpoint; full callback consumption precedes idempotency lookup, and semantic admitted-set hashing allows replay to reuse the stored result. The [actual checks](../../evidence/stream-recovery-run.md) must prove these outcomes; Spark's [callback/checkpoint guide](https://spark.apache.org/docs/4.2.1/streaming/apis-on-dataframes-and-datasets.html) does not itself prove this sink's correctness.

## Controlled backfill

Capture a JSON plan with `stream-backfill-plan`: live and intake generation, dataset/source clock, actual cluster/topic identity, explicit exclusive per-partition offsets and reference seed/arrival/hash. Both readers must have reported through their sink commits and reached the same frontier. Plans with immutable conflicts or unverified live events are refused.

Stop both readers before applying the plan. A database-wide live-writer lock fences live/reference changes; the intake dataset lock freezes the source prefix. Recheck the exact plan under those locks. No broker call is required: repair targets the declared durable prefix, while the broker may have newer data.

For this first small MVP, require a complete zero-based prefix in the registered topic epoch: unique offsets, minimum zero and count/maximum agreeing with every requested exclusive offset. Revalidate raw bytes with the shared decoder and compare the resulting canonical map with the entire dataset's intake ledger. Refuse missing transport history, canonical drift, immutable collisions or canonical history from another epoch. Limits are 20,000 deliveries and 64 MiB of key/value bytes; these are bounded repair input limits, not a total memory guarantee. General partial, multi-epoch or retention-loss repair is deferred.

Insert only missing canonical events with admission origin `backfill`. Preserve all existing event content and first-commit timestamps. Rebuild complete payment histories and minute buckets for this small generation using the shared deterministic SQL; do not promote malformed records or alter financial batch tables. The admitted ledger, projections and completed repair audit commit together. Failure rolls everything back; failed attempts remain in caller logs rather than a partially committed repair row.

Audit grain is one completed repair ID: request hash, exact plan, reason, generation, source digest, before/after counts and fingerprints, completion time and status. A retry with the same ID/request returns the original summary without rewriting rows; changed requests under that ID are refused. Backfill does not write checkpoint files or Spark batch/progress records. Resume the same checkpoint afterwards; retained PostgreSQL uniqueness protects backfilled events from later re-delivery.

## Validation and limits

Prove state and applied-minute parity against an actual batch run from the same fixed source fixture, source cutoff and seeded reference frontier in a separate disposable database. Derive minute eligibility from batch events/state/illegal transitions independently of the live application table. A historical replay is not a latency benchmark.

This slice addresses A10/A11 for the bounded fixture. Repeated state-eviction testing (A12), full-month replay, performance, backup/archive, arbitrary checkpoint corruption, broker/disk loss and operator-approved immutable-conflict resolution remain separate work. A completed repair does not establish completeness beyond its recorded frontier.
