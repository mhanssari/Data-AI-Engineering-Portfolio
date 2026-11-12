# 0001 Shared portfolio infrastructure, separate case workloads

Status: accepted

Date: 12 November 2026

## Context

The owner prefers infrastructure such as Kafka and Airflow to be separate resources shared by all portfolio projects. Airflow already runs from `platform/airflow/`; Kafka has not been implemented. Duplicate stacks would add maintenance and memory use on the same laptop.

## Options

1. A separate infrastructure stack for each case.
2. Infrastructure owned by one case and consumed by the others.
3. Portfolio infrastructure under `platform/`, with separate case workloads and state.

## Decision and rationale

Option 3, following the owner's preference. Cases that need a service use its shared portfolio stack. A case must remain reproducible without running another case's application or data pipeline; it may depend on documented shared infrastructure.

| Resource | Portfolio responsibility | Case responsibility |
| --- | --- | --- |
| Airflow | Scheduler, UI, metadata database and infrastructure configuration in `platform/airflow/` | DAGs, isolated Python environments, connection settings and run outputs |
| Kafka | Broker, infrastructure configuration and lifecycle in `platform/kafka/`, when streaming begins | Case-prefixed topics and consumer groups, event contracts, topic retention requirements and replay procedures |
| Spark | Reusable runtime image where dependencies are compatible | Jobs, dependency versions, checkpoints and execution state; no shared Spark cluster is required by this decision |

Case 03's PostgreSQL remains case-owned. This decision does not move existing databases or other services into a shared stack automatically.

## Operating boundaries

- Each case documents the shared prerequisites and its own run commands. Batch CLIs remain usable without Airflow; Kafka is required only for cases' streaming paths.
- A case's reset or replay must affect only its own data, topics, consumer groups, checkpoints and outputs.
- Service shutdown and shared-volume deletion are platform operations. Case cleanup must not remove shared infrastructure state.
- Credentials stay outside Git and are supplied through local configuration. Case dependencies remain isolated from Airflow's environment and other cases.
- Services start only when needed within the laptop's memory budget. Sharing local services does not establish production isolation, high availability or a cloud deployment.

## Implementation state and next step

- Shared Airflow exists; Case 02's backfill was recorded on it. See [the platform run guide](../airflow/README.md).
- Case 03 now has a DAG in that stack; its [run guide](../../cases/03-payment-reconciliation/orchestration/README.md) documents the late-file recovery and rerun checks. Case 03's database CI workflow is the next slice.
- Kafka remains planned. Introduce `platform/kafka/` at Case 03 milestone 2, then document and validate topic isolation and replay. Broker versions, watermark, retention and checkpoint details are selected at that milestone.

The earlier [Case 03 Airflow decision](../../cases/03-payment-reconciliation/docs/decisions/0001-shared-airflow.md) retains its implementation rationale. This portfolio decision owns the general sharing policy.
