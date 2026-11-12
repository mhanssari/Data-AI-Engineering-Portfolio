# Shared portfolio platform

Infrastructure used by the portfolio's cases lives here. Workloads, dependencies, configuration and state remain separately owned by each case, as defined in [decision 0001: shared infrastructure](decisions/0001-shared-infrastructure.md).

| Resource | State | Guide |
| --- | --- | --- |
| Airflow | Shared local stack implemented; Case 02 and Case 03 integrated | [Airflow](airflow/README.md) |
| Kafka | Planned for Case 03 milestone 2 | [Sharing policy](decisions/0001-shared-infrastructure.md) |

Start only the resources the selected case needs. Each case documents its own prerequisites, execution and recovery commands.
