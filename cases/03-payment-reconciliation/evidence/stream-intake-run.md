# Streaming runtime and durable intake: local validation

Recorded 12 November 2026 on Windows with Docker Desktop, using Linux containers. **The optional producer/Spark runtime and unwatermarked intake are validated locally and on GitHub.** Committed/pushed as `1848bd9`. Its first [Case 03 Streaming run](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43257252331) passed all three real drains, quarantine, restart and re-publication checks. [Database CI](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43257252189) passed 68 tests in 141.20 seconds, with PostgreSQL required. [Platform Kafka](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43257252008) and [Platform Airflow](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43257252024) also passed. This evidence covers the intake commit; subsequent live-query work is recorded [separately](stream-live-run.md).

## Prior foundation delivery

Committed and pushed shared Kafka as `74b55aa` on `case-03-payments`. Both the [Platform Kafka check](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43250159724) and [Case 03 database CI](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43250159511) passed. Those runs cover the foundation commit, not this new intake code.

## Runtime and isolation

- Python 3.12.16; PySpark 4.2.1; Scala-2.13 Spark Kafka connector 4.2.1; connector Kafka client 3.9.2; confluent-kafka 2.16.1; OpenJDK 21.0.13. Build actually started Spark 4.2.1 and resolved 11 connector/transitive JARs (62,888 KiB) from Apache Maven Central.
- Python packages are locked by `uv.lock`, installed only when the `streaming` group is selected. The JRE Debian patch and Maven transitives are not digest-locked. Builds require network access; query startup uses existing image JARs.
- Non-root case image; Spark `local[2]`, three shuffle partitions, 1 GiB driver heap, 3 GiB container limit. Shared broker remains a single Kafka 4.3.2 node with a 1.5 GiB container limit. No measured peak-memory or distributed-cluster claim.
- Separate `payments_stream` database and case-owned `payment-reconciliation_stream-state` volume. The overlay joins the platform's Kafka network only when requested. Existing `payments` and `payments_airflow` databases were preserved; Airflow stayed stopped.
- Rebuilt the batch image with the new lock. An image-only Python probe asserted that `pyspark` and `confluent_kafka` were absent and `java` was unavailable, then successfully ran CLI help. No host Spark/JRE/Kafka package was installed.

## Commands and observed results

From the case directory, built both `Dockerfile.streaming` through its optional overlay and the default batch image. Started shared Kafka and the existing case PostgreSQL. Followed the [intake run guide](../streaming/README.md) for preparation, publication and drain.

The initial real fixture producer acknowledged all eight records. Actual cluster ID was `MkU3OEVBNTcwNTJENDM2Qk`, diagnostic topic `case03.intake-check` had ID `Hlekr1KeTfKljhdWkPEPEQ`, and offsets were partition 2: 0–6 and partition 1: 0. Intake generation `65d538d3-4f93-4cae-a175-1bfe1bafb6ca` committed batch 0 with eight new deliveries, three new events, one duplicate and four non-blocking quarantine rows. Source-clock lag for the retained authorisation was 86,400 seconds.

The first real drain exposed a logging error: Spark progress contains UUID values and plain `json.dumps` failed **after** the database and Spark checkpoint had committed. Fixed progress serialisation; did not count that failed command as a successful drain. The complete isolated check below then exited zero for all three drains and verified durable results across real checkpoint restarts.

From the repository root, with services healthy and the optional image built:

```sh
python cases/03-payment-reconciliation/streaming/check_intake.py
```

The host helper uses only the standard library and Docker. It creates a unique `case03.intake-check-*` topic, `payments_stream_check_*` database and `/state/check-*` checkpoint. Results:

| Stage | Deliveries | Canonical events | Quarantine | Blocking | Events with source lag over ten minutes |
| --- | ---: | ---: | ---: | ---: | ---: |
| First acknowledged publication and Spark drain | 8 | 3 | 4 | 0 | 1 |
| Restart with the same checkpoint | 8 | 3 | 4 | 0 | 1 |
| Republish and drain with the same checkpoint | 16 | 3 | 8 | 0 | 1 |

The restart snapshot was identical, including first-commit timestamps. Canonical digest stayed `10036443a59e7aed392577734208797b` after re-publication; lineage count stayed one. The helper reported all checks passed and exited 0, including scoped cleanup of its unique database, topic and checkpoint. Raw malformed bytes, string money, tombstones and unknown-version values were retained in delivery/quarantine. No ten-minute Spark drop occurred or is inferred: this query deliberately has no watermark.

Full financial/unit/database suite, from the case directory:

```sh
docker compose run --rm --no-deps -e PAYRECON_REQUIRE_DB=1 --entrypoint pytest payrecon -q --junitxml=evidence/ci/intake-pytest.xml
```

**68 passed in 331.76 seconds; zero failures, errors or skips.** JUnit records 68 tests and 331.826 seconds. The 21 new cases cover contract coercion/clock/hash/version/key rejection, retained late input, exact callback replay, immutable ID/sequence conflicts, changed physical input, injected failure after raw COPY with complete rollback, repeated migration and checkpoint/topic/metadata guards. These tests use disposable PostgreSQL databases and require no Spark imports. They do not replace real Kafka integration or future crash-at-checkpoint tests.

Ruff lint passed; formatting reported 63 files already formatted. Both base and streaming-overlay Compose configurations validated. `actionlint` passed for the new workflow. Git whitespace checks passed, and all 108 relative Markdown links in the changed/new documentation and local handoff files resolved.

## Guarantees, limits and end state

Batch effects, raw bytes and quarantine share a PostgreSQL transaction with the intake commit record. Replayed committed callback input is checked and its stored outcome reused. Physical transport and canonical identities have separate uniqueness guards. Conflicting immutable identities are archived and stop the affected query without replacing canonical history. A dataset lock excludes a second intake writer; checkpoint sidecar and database lineage bind actual topic/cluster identity.

Filesystem checkpoints and PostgreSQL are separate durability domains; marker registration is not atomic across them. Missing or changed lineage fails visibly, and arbitrary checkpoint corruption is not detected. No backup, retention-expiry recovery, broker-loss recovery, automatic conflict repair, watermark/backfill parity, two-query frontier consistency, latency or throughput benchmark has been implemented by this slice. A9–A12 remain open.

The retained demo has eight deliveries, three canonical events, four quarantine rows and no blocking conflict in `payments_stream`, plus its original checkpoint/sidecar. Its topic is separate from the still-empty `case03.payment-events.v1`. Isolated integration fixtures were removed. Shared Kafka is stopped after validation with broker data/network and checkpoint volume preserved; PostgreSQL remains running and Airflow remains stopped.

The intake commit and its first CI run are now confirmed. Next application work is the independent ten-minute watermarked live query with deterministic SQL state/minute projections; its [separate evidence](stream-live-run.md) owns current validation and remaining recovery work. No business decision blocks that sequence.
