# 0008 Optional streaming runtime and durable intake

Status: accepted for the implemented intake slice

Date: 12 November 2026

The [two-query design](0007-streaming-correctness-and-recovery.md) needs a durable record of input before the live query can exclude late events. Keep this first application slice independently runnable and separate from financial writes.

Use a case-owned Docker image with the opt-in `streaming` dependency group: Python 3.12, PySpark 4.2.1, matching Scala-2.13 Kafka connector 4.2.1, OpenJDK 21 and confluent-kafka 2.16.1. The connector retains its declared Kafka client 3.9.2; it is not overridden to match shared broker 4.3.2. Build resolves connector dependencies from Apache Maven Central; jobs use the resulting JARs without runtime downloads. Python packages are locked by `uv.lock`; the JRE's Debian patch and Maven transitive artefacts are not locked by digest. Record actual runtime versions in [evidence](../../evidence/stream-intake-run.md).

One optional Compose service joins the shared broker network, uses Spark `local[2]` and a separate `payments_stream*` database, and persists checkpoints in a case-owned named volume. Batch and shared Airflow retain their smaller independent environments. Start with a tiny acknowledged fixture, including duplicates, poison and a day-late event; do not call it a live-latency benchmark.

The intake has no watermark and no stateful Spark operator. It consumes bounded batches through the driver and performs one PostgreSQL transaction for delivery bytes, canonical events, quarantine and the batch ledger. Transport identity is actual cluster ID, topic ID, partition and offset. Dataset/event identity and dataset/payment/sequence identity are immutable. A duplicate preserves canonical content and timestamps; a conflicting identity commits blocking evidence, then fails the query. Malformed or unsupported envelopes are quarantined without stopping other valid input.

A database session lock fences concurrent intake writers for the dataset. Checkpoint sidecar, database generation, source clock and actual broker identity must agree. Missing or changed lineage fails closed. PostgreSQL commits and checkpoint files are separate durability domains: registration is not atomic across them, arbitrary checkpoint corruption is not detected, and ambiguous states need explicit recovery. Repeated committed callbacks compare physical input and return the recorded result; changed input under the same batch ID is refused.

This implements replay-safe intake database effects under retry, not end-to-end exactly-once delivery. Broker retention, disk loss, backup/archival and operator-approved conflict repair are separate concerns. The [design](../streaming-design.md) remains authoritative for the future live grains, metric definitions and ten-minute lateness policy; live projections, watermark eviction, late backfill, full fault injection and A9–A12 are not completed by intake alone.
