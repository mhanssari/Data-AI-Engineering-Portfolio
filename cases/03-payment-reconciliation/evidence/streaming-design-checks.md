# Streaming design checks

Recorded 12 November 2026. These are source profiling and availability/documentation checks, not streaming runtime validation. No Kafka/Spark service, dependency lock or migration was added in this slice.

Subsequent broker implementation and its actual runtime checks are recorded separately in [Kafka foundation evidence](../../../platform/kafka/evidence/foundation-run.md). The original metadata observations below remain a design-stage record.

## Owner choices

- Confirmed authorisation decline rate with a separate expiry count, replacing the draft cross-time failure ratio.
- Confirmed a ten-minute live event-time lateness window, with durable intake and controlled backfill for later events.

Engineering topology, retention and runtime values remain reversible proposals in the [streaming design](../docs/streaming-design.md).

## Existing source delay profile

Ran `build_world()` from the existing case's Linux container, seed 3303, and profiled `ingestion_time - event_time` without generating or changing source files:

| Measure | Value |
| --- | ---: |
| Source world events, before file-level duplicate delivery | 177,325 |
| Minimum source delay | 0 seconds |
| Median / p95 source delay | 45 / 86 seconds |
| Maximum source delay | 86,400 seconds |
| Events delayed more than ten minutes | 1 |

The sole event over ten minutes is `evt-900000202`, P2's authorisation, at 86,400 seconds. Ordinary background delays are at most 90 seconds (with end-of-day clamping in the generator). This supports a short live-state horizon and a deliberate recovery example; it does not prove that Spark will drop a particular event without a controlled watermark/offset-frontier test. These are source-clock measurements, not Kafka/Spark latency.

## Availability and compatibility evidence

| Candidate | What was checked | Still to validate |
| --- | --- | --- |
| `apache/kafka:4.3.2` | Official maintenance release and `docker buildx imagetools inspect`; Linux amd64/arm64 manifests exist | Startup, listeners, producer/consumer protocol, persistence and memory |
| PySpark `4.2.1` | [Official installation guide](https://spark.apache.org/docs/4.2.1/api/python/getting_started/install.html) and [PyPI metadata](https://pypi.org/pypi/pyspark/4.2.1/json): Python 3.12 is supported; source distribution exists | Install/build on the existing Python 3.12 image and actual streaming execution |
| Matching Spark Kafka connector | [Official linking guide](https://spark.apache.org/docs/4.2.1/streaming/structured-streaming-kafka-integration.html), plus successful HTTP retrieval of the [Maven POM](https://repo.maven.apache.org/maven2/org/apache/spark/spark-sql-kafka-0-10_2.13/4.2.1/spark-sql-kafka-0-10_2.13-4.2.1.pom) | Resolve the full artifact tree during image build and smoke-test it with the broker |
| OpenJDK 21 | Spark permits Java 17+; [Debian trixie package](https://packages.debian.org/trixie/openjdk-21-jre-headless) exists for amd64 | Image installation, JAVA_HOME and actual JVM version |
| `confluent-kafka 2.16.1` | [PyPI metadata](https://pypi.org/pypi/confluent-kafka/2.16.1/json): Python >=3.8; `confluent_kafka-2.16.1-cp312-cp312-manylinux_2_28_x86_64.whl` exists | Wheel installation and acknowledged production against the selected broker |

The connector POM declares `spark-sql_2.13`/token-provider `4.2.1` and `kafka-clients 3.9.2`. That client number is not a reason to change the connector's transitive dependency manually. Protocol compatibility must be proved by the first small produce/consume query. Spark 4.2.1 is a patch-line candidate; Spark 4.3.0 is also published, but its additional features are unnecessary for this MVP. No claim is made that the proposed version set has been integrated successfully.

The Kafka image index digest observed was `sha256:852a0d49fc4e656b022e7f23785411802c9a088c78a7c5e39f33969de9c227a7`. No image layers were pulled. Docker reported 8 logical CPUs and 8,225,247,232 bytes allocated (~7.66 GiB), grounding the proposed memory budget.

## Documentation findings

The [Spark streaming guide](https://spark.apache.org/docs/4.2.1/streaming/apis-on-dataframes-and-datasets.html) documents bounded deduplication and at-least-once `foreachBatch`, while the [Kafka source guide](https://spark.apache.org/docs/4.2.1/streaming/structured-streaming-kafka-integration.html) documents generated group prefixes and data-loss handling. These informed separate checkpoints, explicit database idempotency and a durable unwatermarked intake. Kafka's [topic configuration reference](https://kafka.apache.org/43/configuration/topic-configs/) informed per-partition retention and broker append-time proposals.

## Batch handoff

Batch-review commit `95b78f3` was pushed to `case-03-payments`. Both triggered workflows passed: [Case 03 CI](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43242276748) and [Platform Airflow](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43242277017). These validate existing batch/platform code; they do not validate this new streaming design.
