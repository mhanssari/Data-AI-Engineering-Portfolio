# Shared Kafka foundation: local validation

Recorded 12 November 2026 on Windows with Docker Desktop. **The broker foundation is implemented and validated locally.** No payment-event producer, Spark runtime, intake ledger or checkpoint recovery is implemented by this slice.

## Configuration and identity

- Official image: `apache/kafka:4.3.2`, pinned by index digest `sha256:852a0d49fc4e656b022e7f23785411802c9a088c78a7c5e39f33969de9c227a7` in [Compose](../compose.yaml). Actual platform/user: `linux/amd64`, `appuser`; CLI version reported `4.3.2`.
- One combined KRaft broker/controller, node 1; named volume `portfolio-kafka_kafka-data`; Docker client network `portfolio-kafka`. Cluster ID returned by the broker: `MkU3OEVBNTcwNTJENDM2Qk`.
- Actual published binding: `19092/tcp -> 127.0.0.1:19092`; internal advertised client address `kafka:9092`, host advertised address `localhost:19092`. The controller is not published.
- Broker heap: 256–512 MiB; container limit 1.5 GiB. A post-check Docker snapshot reported 306.2 MiB, not a peak or benchmark.

The Kafka [Docker example](https://github.com/apache/kafka/blob/4.3.2/docker/examples/docker-compose-files/single-node/plaintext/docker-compose.yml) informed the listener/KRaft configuration. This records the actual local topology; it has replication factor 1, plaintext and no HA/ACL claim.

## Commands and results

From the repository root:

```powershell
docker compose -f platform/kafka/compose.yaml up -d --wait
python platform/kafka/check_foundation.py --restart
docker compose -f platform/kafka/compose.yaml run --rm -T `
    -v "${PWD}/cases/03-payment-reconciliation/streaming:/case:ro" tools /case/init-kafka.sh
```

The integration check ran with the installed host Python standard library; no host Kafka package or JRE was installed. Producer/consumer tools ran in Linux containers using the pinned Kafka image.

| Check | Observed result |
| --- | --- |
| Startup and readiness | Broker healthy; Compose configuration valid |
| Repeated case initialisation | Same test-topic ID, partitions, replicas and settings |
| Namespace rejection | Case initializer returned 2 for the unrelated test topic; it remained unchanged |
| Configuration drift | A longer retention value on the case test topic caused initialisation to fail without silently restoring it; deliberate test cleanup restored the expected value |
| Internal listener | Independent producer/consumer containers on `portfolio-kafka` roundtripped a unique sentinel message |
| HOST listener | Producer/consumer containers in the broker network namespace roundtripped a unique message through `localhost:19092` |
| Actual host connection | Native Windows-host Metadata v4 request over `127.0.0.1:19092` returned node 1, `localhost:19092` and the expected cluster identity |
| Timestamps | Consumed records included broker `LogAppendTime` timestamps |
| Broker restart | Both messages, topic IDs/configurations and cluster identity survived |
| Unrelated topic | Its identity, settings and message were preserved across case setup and restart |
| Cleanup | Uniquely named diagnostic topics removed; real case topic never used for test messages |

Final check output:

```text
PASS: case topic initialisation is repeatable; unrelated topic unchanged
PASS: configuration drift fails without silently changing the existing topic
PASS: host metadata from the real host: {"cluster_id": "MkU3OEVBNTcwNTJENDM2Qk", "brokers": [{"node": 1, "host": "localhost", "port": 19092}]}
PASS: internal client and HOST-listener container roundtrips; broker append timestamps
PASS: restart preserves cluster/topic identities, settings and both test messages
Kafka foundation checks passed
```

The first harness run detected missing producer input because `docker run` lacked `-i`. Corrected the attachment and used the pinned CLI's current `--formatter-property` option; the complete rerun above exited 0. No failed message check was treated as a pass.

## Actual Case 03 topic

Ran the case initializer twice through the optional `tools` service. Both succeeded with topic `case03.payment-events.v1`, ID `WCWnv4p6QT2xhtLf349lLQ`, three partitions and replication factor 1. The six explicit case settings matched the [initializer](../../../cases/03-payment-reconciliation/streaming/init-kafka.sh). No automatic configuration repair or deletion occurred.

After diagnostic cleanup, topic listing contained only this case topic. Latest offsets were `0:0`, `1:0`, `2:0`: all three partitions are empty. No synthetic payment producer has run yet.

## Static checks and CI boundary

Ruff lint and formatting passed for `check_foundation.py`; `actionlint` passed for the new [workflow](../../../.github/workflows/platform-kafka.yml); Compose validation and Git whitespace checks passed. The default Case 03 batch Compose still validates with no Kafka dependency. The broker check is the relevant integration validation; the unchanged 47-test financial suite was not repeated locally in this slice.

Streaming-design commit `b9aa48c` was pushed separately and its existing [Case 03 CI](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43246414657) passed. The new Kafka implementation/workflow remains local and uncommitted; it has not run on GitHub.

## Limits and end state

The host protocol probe and container HOST-listener roundtrip are distinct checks; this is not a full native Windows producer/consumer test. The unrelated topic is a generated isolation fixture, not another real project's data. No retention-expiry, broker/disk-loss, multi-broker failover or Spark recovery test is claimed.

Broker stopped after validation, preserving its volume, network and empty Case 03 topic. Original PostgreSQL and shared Airflow state were preserved. Next slice: build and validate the optional case Python/JRE/Spark/connector runtime, then implement durable unwatermarked intake before the live query.
