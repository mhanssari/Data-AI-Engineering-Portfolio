# Current-clock streaming measurement

Measured locally on 12 November 2026. Three completed runs, 600 measured events each: **broker-to-visible p95 6.79–7.06 seconds**, below the initial engineering target of 15 seconds at this small load. Every run retained and applied all 800 events including warm-up. This is a finite single-live-reader result, not maximum capacity or concurrent intake/live performance.

## Environment and method

Intel Core i7-10510U, four physical cores/eight logical processors, 16,977,625,088 bytes physical RAM (15.81 GiB). Docker Desktop Linux/WSL2 exposed eight CPUs and 8,225,247,232 bytes (7.66 GiB), kernel `6.6.87.2-microsoft-standard-WSL2`, Docker Server 29.9.2. Airflow stayed stopped. Shared Kafka and case PostgreSQL were already healthy; each repeat used a fresh diagnostic topic/database/checkpoint/JVM. Neither caches nor OS state were flushed.

Actual runtime: Kafka 4.3.2 (pinned shared image), PostgreSQL 18.6, Python 3.12.16, Spark/Scala-2.13 Kafka connector 4.2.1, confluent-kafka 2.16.1, OpenJDK 21.0.13. Kafka: three partitions, replication factor one, LogAppendTime, 512 MiB maximum heap/1.5 GiB container. Live Spark: `local[2]`, three shuffle partitions, 1 GiB driver/3 GiB container, five-second triggers, 3,000 offsets per trigger. PostgreSQL had no container memory cap. The separate producer/observer container used the same optional image/3 GiB limit without starting a JVM. Container limits are configuration, not measured peak memory.

The [measurement decision](../docs/decisions/0012-current-clock-measurement.md) owns the workload and repeat policy; the [metric catalogue](../docs/metrics.md) owns definitions. Each repeat offered five new payments/second, two current-clock events/payment: initiation followed by authorisation or decline, 75%/25%, M001, GBP, 4,200 minor units. All three used the same deterministic payment/event IDs in separate databases and topic epochs. No defects or duplicates were introduced in this measurement; separate correctness checks cover them.

Warm-up submitted 200 events over 20.02–20.03 seconds, then waited until all were acknowledged and visible. Measured input began 56.77, 25.29 and 27.34 seconds after the first warm-up submission respectively. The first run's cold batch alone took 26.154 seconds; cold startup/warm-up is excluded, rather than hidden inside a steady-processing claim. Each measured phase then submitted 600 events over at least 60 seconds, without catch-up bursts. Maximum scheduling delays were 10.56, 10.76 and 7.83 milliseconds.

A separate autocommit connection observed committed applied output roughly once per second. Its first visibility timestamp came from PostgreSQL `clock_timestamp()`. Intake ran only after live stopped, providing broker timestamps and transport-coordinate validation without competing for Spark resources during measurement. Existing `now()` transaction-start metadata was not used as a commit endpoint. Visibility includes observer delay and is an observed upper bound on commit latency under the common Docker-VM clock assumption; it is not an exact commit timestamp.

## Results

Each latency distribution contains all 600 measured events, excludes all 200 warm-up events and uses nearest-rank percentiles. There were no missing or negative latency samples. Rates below describe the offered load and finite output/drain interval; they do not establish a maximum sustainable rate.

| Repeat | Offered events/s | Observed events/s including drain | Broker→visible p50 / p95 / max (s) | Event→visible p50 / p95 / max (s) |
| --- | --- | --- | --- | --- |
| 1 | 9.981 | 9.560 | 4.625 / 7.062 / 8.027 | 4.631 / 7.069 / 8.034 |
| 2 | 9.985 | 9.565 | 4.637 / 6.935 / 7.450 | 4.646 / 6.942 / 7.458 |
| 3 | 9.986 | 9.122 | 4.433 / 6.793 / 7.411 | 4.442 / 6.799 / 7.417 |

Maximum actual observation gaps were 1.032, 1.026 and 1.031 seconds. Maximum database clock-check round trips were 10.936, 4.430 and 4.625 milliseconds; server times fell within client before/after brackets with the explicit 2 ms tolerance. Broker timestamp ordering relative to publication passed the 2 ms millisecond-precision check. These are sanity checks, not independently calibrated clock accuracy.

Maximum submitted-but-not-yet-observed counts during measured input were 72, 64 and 62; each finished at zero. These include publishing/in-flight/polling delay and are not broker-head backlog. Last acknowledged delivery to all-output-visible took 2.845, 2.840 and 5.885 seconds. After drain, both readers matched the producer's acknowledged exclusive frontier in every repeat: partitions 0/1/2 = **242/294/264**. Each fresh topic began at zero, so all 800 physical deliveries were accounted for.

Every repeat finished with 800 raw deliveries, 800 canonical intake events, 800 admitted/applied live events, 400 payment states, 300 authorisations, 100 declines and zero expiries (25% aggregate decline rate). Recorded-frontier consistency was true. There was no repair, conflict, missing event or unexplained output loss. The host helper exited zero after all three runs and cleaned their diagnostic resources.

The [machine-readable summary](stream-measurement-summary.json) preserves configuration, exact per-repeat results, identities, frontiers and SHA-256 hashes of the full ignored reports. Per-event records, acknowledgements, clock brackets and poll series remain at `evidence/ci/measurement-1.json`, `measurement-2.json` and `measurement-3.json`; live logs are `measurement-live-<token>.log`. Run the documented command in the [streaming guide](../streaming/README.md) to reproduce the profile with new identities and timings.

## Limits and next step

Validation after the three runs: **52 streaming tests passed in 34.55 seconds**, with PostgreSQL required (47 unrelated tests deselected); Ruff lint/format passed (93 files); the combined Compose configuration passed and 117 relative Markdown links resolved. The financial SQL and production runtime were unchanged. The earlier A12 commit's separate GitHub database check passed 97 tests and its streaming workflow passed; the new measurement tests have not yet run remotely.

Retained demonstration data remained at 16 deliveries, three canonical events and eight quarantine rows, with canonical digest `1f27ca689a124e180370e8a6b0cea38b`. The primary topic remained empty at all three partition offsets. All three measurement topics/databases, checkpoint roots and labelled containers were absent after cleanup; shared volumes and original checkpoints were preserved. Kafka was returned to its prior stopped state; PostgreSQL remained healthy and Airflow stayed stopped.

This measures a single driver/PostgreSQL sink at one small offered load over three short intervals. It covers warm processing after declared warm-up, with an observer running alongside the producer. It does not establish cold-start latency, capacity headroom, high availability, long-duration stability, peak RAM, intake latency, concurrent reader performance, full-month replay or cloud performance. Financial transformations, sink transactions and recovery rules were unchanged.

Next: commit the measurement slice, confirm its ordinary CI checks, then perform the final clean-checkout streaming review and case handoff. A separate full-month historical replay remains optional later validation.
