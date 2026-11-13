# Runtime locations

Local case resources are defined by [base Compose](../compose.yaml), [the batch image](../Dockerfile), [the optional streaming overlay](../streaming/compose.yaml) and [streaming image](../Dockerfile.streaming). PostgreSQL and checkpoint state belong to this case.

Airflow and Kafka are separate shared portfolio services under [platform](../../../platform/README.md), following [decision 0001](../../../platform/decisions/0001-shared-infrastructure.md). Configuration uses the case's ignored `.env`; the committed [example](../.env.example) contains dummy values. No cloud resources or infrastructure-as-code deployment exist in this MVP.
