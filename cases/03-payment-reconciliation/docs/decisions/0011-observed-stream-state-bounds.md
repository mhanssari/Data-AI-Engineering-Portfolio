# 0011 Observe state eviction through a continuously running query

Status: accepted for the bounded A12 validation profile

Date: 12 November 2026

One eviction after a large time jump demonstrates a mechanism. A12 also needs evidence that adding fresh keys across successive windows evicts older keys repeatedly, without deleting business history. Use the actual case live query with its existing ten-minute watermark and single bounded deduplication operator. No processing or financial rule changes are needed.

Run one continuous live process with one Spark query ID and run ID. Publish eight deterministic cohorts seven event-time minutes apart: 24 new payments and 48 unique events per cohort, with 18 authorisations and six declines. Event IDs and payment IDs never recycle across cohorts. This spans 49 minutes between cohort starts; it is accelerated historical input, not a latency or throughput benchmark.

After each acknowledged cohort, wait for the source's explicit exclusive offset frontier **and** the reported cleanup watermark. Record retained state rows, rows removed, operator memory, query/run identity and durable ledger counts. Require repeated removal of at least one cohort after warm-up, at most four cohorts (192 keys) in settled state and at most five cohorts (240 keys) in intermediate reports. These are conservative test guards for this fixed density/key size, not universal Spark guarantees. Do not assume that a ten-minute watermark retains exactly ten minutes of deduplication keys; let reported state establish the actual result.

Keep the live process running during a short idle observation and both duplicate probes. Re-send the newest cohort within the watermark: duplicates must not change the admitted ledger or minute totals. Re-send the oldest cohort after eviction: reported watermark drops must increase, with durable totals unchanged. Source-clock lag alone is not evidence of a late drop. Record state-operator bytes as observations; they are not total JVM/container memory or a hard RAM bound under arbitrary key arrival rates.

The first continuous check exposed a reporting defect: Spark's no-execution idle reports can use the next batch ID. Marking that ID as seen hides the later real callback report. The logger now filters for the `addBatch` duration before persisting/marking a report; actual empty watermark-cleanup callbacks still qualify. Keep a focused regression for idle → real callback with the same ID, and prove the fix through the continuous duplicate probe. This changes telemetry/frontier reporting, not event admission or SQL money/state rules.

Drain durable intake after stopping the continuous reader, so only one Spark workload runs at a time on the laptop. The broker holds the bounded input meanwhile. Expect 480 physical deliveries (384 original events and two 48-record re-sends), 384 canonical events, 192 payment states and eight operational minute buckets. Applied decision rate is 25% per minute and expiries are zero under the [metric definitions](../metrics.md). Intake and PostgreSQL event history remain retained while Spark keys are evicted. Resume the same live checkpoint afterwards and verify unchanged business fingerprints.

The checker owns a unique diagnostic topic/database/checkpoint root and one labelled container. Cleanup stops only that container and removes only its own test resources. CI retains JSON observations and the continuous consumer log. [Evidence](../../evidence/stream-state-run.md) owns actual results and scope.

Spark's [streaming guide](https://spark.apache.org/docs/4.2.1/streaming/apis-on-dataframes-and-datasets.html#streaming-deduplication) explains event-time deduplication and watermark-driven eviction. The local profile does not prove arbitrary-rate memory bounds, long-duration stability, checkpoint-disk bounds, broker retention expiry, database archival or production-scale operation. Kafka's configured finite retention and the retained database ledger have separate lifecycle responsibilities.
