# Shared local Kafka

One Kafka 4.3.2 KRaft broker/controller for portfolio cases, pinned by image digest in [compose.yaml](compose.yaml). It has a named data volume, a 512 MiB maximum JVM heap and a 1.5 GiB container limit. This is a local demonstration with replication factor 1, plaintext listeners and no HA. Namespaces separate ownership; they do not enforce access control.

| Client | Bootstrap / advertised address | Exposure |
| --- | --- | --- |
| Case containers on `portfolio-kafka` | `kafka:9092` | Shared Docker network |
| Tools on this machine | `localhost:19092` | Published on `127.0.0.1` only |
| Controller | `kafka:9093` | Internal; not published |

The platform owns the broker, network, cluster identity and storage. Cases own topic creation/configuration, clients and checkpoints. See [decision 0002](../decisions/0002-shared-kafka-topology.md). The cluster ID is a stable local identifier, not a password; do not change it against an existing data volume.

## Start and initialise a case

Docker is required. From the repository root:

```powershell
docker compose -f platform/kafka/compose.yaml up -d --wait
docker compose -f platform/kafka/compose.yaml run --rm -T `
    -v "${PWD}/cases/03-payment-reconciliation/streaming:/case:ro" tools /case/init-kafka.sh
```

The optional `tools` profile runs only CLI commands in the same pinned Kafka image; it has no broker data-volume mount. Default startup creates no case topics. Case 03's [initializer](../../cases/03-payment-reconciliation/streaming/init-kafka.sh) creates `case03.payment-events.v1` if absent and verifies its three partitions, replication factor and six explicit retention/timestamp settings. Repeating it changes nothing. Existing configuration/shape mismatches fail without altering the topic. It refuses names outside `case03.`.

Case 03 uses seven-day nominal retention with a 512 MiB per-partition size threshold, 64 MiB segments and one-hour segment rolling. Kafka may remove data earlier due to the size threshold, and segment/deletion granularity needs additional disk headroom. Broker append time drives retention; payload event time is independent. This is not a seven-day archive guarantee. Topic settings are authoritative in the case initializer.

Batch cases do not need this service. A future case's optional streaming Compose configuration can join the external network named `portfolio-kafka`; start this platform first. Do not add Kafka as a dependency of a case's default batch Compose.

## Foundation checks

The optional [check script](check_foundation.py) requires Python 3.10+ with the standard library and the Docker CLI on the host; no Python packages are installed. All producer/consumer tools run in Linux containers. It deliberately restarts the shared broker, so run it only when other cases are not using Kafka:

```powershell
python platform/kafka/check_foundation.py --restart
```

It verifies case-topic idempotency, namespace rejection, refusal of configuration drift, unrelated-topic preservation, actual host metadata and advertised addresses, two listener roundtrips and persistence over a restart. It creates uniquely named diagnostic topics and deletes only those topics on completion; it never writes test messages into the real Case 03 event topic.

The internal producer/consumer run in independent client containers. HOST-listener producer/consumer checks run in containers sharing the broker's network namespace so `localhost:19092` has the same meaning as its advertised endpoint. A separate Kafka Metadata v4 request from the real host checks the published loopback path and response. This does not claim that a full Kafka client was installed or run natively on Windows.

The [Kafka workflow](../../.github/workflows/platform-kafka.yml) runs the same checks on an isolated Ubuntu runner. Its first GitHub result must be confirmed after the implementation is pushed; local success alone is not remote CI evidence.

## Stop and preserve data

```powershell
docker compose -f platform/kafka/compose.yaml stop
```

Stopping preserves `portfolio-kafka_kafka-data`, topic IDs and messages. Removing the shared volume is a deliberate platform reset affecting every case; ordinary case cleanup must not do it. Do not run two broker stacks against the same volume or reuse existing checkpoints after an unrecorded topic/cluster reset.
