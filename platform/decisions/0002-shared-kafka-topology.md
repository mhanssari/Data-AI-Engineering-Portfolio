# 0002 Shared local Kafka and optional case networking

Status: proposed engineering design; shared ownership accepted by the owner

Date: 12 November 2026

## Context and options

The owner wants Kafka shared across portfolio projects, like Airflow ([decision 0001](0001-shared-infrastructure.md)). Options were a broker per case, a shared broker reached through published host ports, or a shared broker with an optional client network. Use the last option: broker metadata must advertise an address reachable by its consumers, and an internal Docker address avoids host-routing differences.

## Proposed topology

- `platform/kafka/` will own one combined KRaft broker/controller, its named data volume and a Docker network named `portfolio-kafka`. No ZooKeeper or separate Spark cluster.
- Other cases join that network only through optional streaming configuration. Case 03's default batch Compose remains independent of Kafka and its network.
- Internal clients use `kafka:9092`; a separate host listener advertises `localhost:19092` and is published only on `127.0.0.1`. The controller listener is not published. Validate both metadata paths in the first slice.
- Case 03's streaming container joins both this network and its own database network. PostgreSQL remains case-owned; the stream uses an isolated `payments_stream` database. Airflow keeps its existing host-port approach; this does not change decision 0001's Airflow implementation.
- Topics, consumer-group prefixes and topic-creation specifications belong to each case. Case 03's proposed topic is `case03.payment-events.v1`, keyed by payment ID, with three partitions and replication factor 1. No automatic topic creation or topic deletion on ordinary startup.
- Default case-topic retention proposal: delete policy, seven days by broker append time, 512 MiB per partition, 64 MiB segments and one-hour segment rolling. Size/time limits can expire records earlier than seven days, and segment/deletion granularity requires disk headroom. These are local workload defaults, not durable archival guarantees. Topic-level settings avoid imposing one policy on every case.
- Broker timestamps use `LogAppendTime`; historical replay event time remains in the payload. This prevents old fixture timestamps being treated as old broker arrival times.

This network is a prerequisite only for streaming workloads that explicitly require the broker. It does not create a dependency between case applications. Topic naming is logical separation, not an ACL security boundary. The local plaintext broker has no HA or production access-control claim.

## Candidate and validation gates

Candidate image: `apache/kafka:4.3.2`, a maintenance release. Its registry manifest was inspected on 12 November: Linux amd64 and arm64 exist; index digest `sha256:852a0d49fc4e656b022e7f23785411802c9a088c78a7c5e39f33969de9c227a7`. No image was pulled, broker started or runtime configuration pinned in this design slice. Verify the candidate and record the actual digest at implementation.

First implementation must prove startup/health, produce/consume through internal and host listeners, restart persistence, repeatable case-topic initialisation, and preservation of another case's test topic. Case cleanup must never delete the broker volume or other topics. Only a platform operation may stop/remove shared infrastructure.

The [Kafka topic configuration reference](https://kafka.apache.org/43/configuration/topic-configs/) documents retention and broker timestamp settings. Exact operational limits remain to be measured locally. The [case streaming design](../../cases/03-payment-reconciliation/docs/streaming-design.md) owns its workload and recovery policy.
