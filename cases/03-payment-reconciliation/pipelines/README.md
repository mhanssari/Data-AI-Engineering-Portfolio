# Pipeline locations

The batch [Airflow DAG](../orchestration/dags/payment_reconciliation.py) and [run guide](../orchestration/README.md) live under `orchestration/` and use the shared portfolio scheduler. The DAG handles the finite arrival calendar, file readiness, retries and reconciliation.

Streaming is a separate long-running/AvailableNow CLI workload, described in the [streaming guide](../streaming/README.md). Airflow does not schedule individual payment events. Nothing is deployed as an always-on cloud pipeline.
