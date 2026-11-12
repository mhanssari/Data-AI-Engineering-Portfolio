# Orchestration (opt-in)

Airflow runs the same steps as `loyalty run-all`, one DAG run per extract delivery ([decision 0003](../docs/decisions/0003-orchestration.md)). Nothing else in the case needs it.

| File | Purpose |
| --- | --- |
| [dags/retail_loyalty.py](dags/retail_loyalty.py) | The DAG: extracts, load the run's batch, source freshness, staging and snapshot, marts, export. Each task calls the case's CLI |
| [Dockerfile](Dockerfile) | `apache/airflow:3.4.0-python3.12` with the case in its own environment, `/opt/loyalty-venv`, because dbt and Airflow pin different versions of shared libraries |
| [compose.yaml](compose.yaml) | Airflow standalone and a PostgreSQL metadata database, bound to 127.0.0.1 |
| [check_dag.py](check_dag.py) | CI: the DAG imports and has the agreed shape, with no database |
| [verify_a15.sh](verify_a15.sh) | Acceptance A15: batches in order, marts equal to `run-all`, a rerun changes nothing |

## Run it

Needs Docker and about 4 GB of memory. From this folder:

```powershell
docker compose up -d --build
docker compose exec airflow airflow dags unpause retail_loyalty
docker compose exec airflow airflow backfill create --dag-id retail_loyalty `
    --from-date 2026-09-01 --to-date 2026-10-01 --max-active-runs 1 --reprocess-behavior failed
docker compose exec airflow bash /opt/loyalty-project/orchestration/verify_a15.sh   # once both runs succeed
docker compose down -v
```

The UI is at http://127.0.0.1:8080, with no login: every user is an admin, and that is acceptable only because the port is bound to this machine.

## How it schedules

- **One run per delivery.** An `EventsTimetable` lists the two delivery dates, 16 and 30 September 2026. A run's logical date selects its batch, so a backfill over September creates exactly two runs.
- **In order.** `max_active_runs=1` and `depends_on_past` keep batch 2 behind batch 1. The snapshot needs this, and DuckDB allows one writer. Airflow 3 asks a backfill of such a DAG to say what to do with earlier runs: `--reprocess-behavior failed` reruns only failures.
- **Rerun safety.** Clearing an interval reloads nothing (checksums), the snapshot records nothing new, and the incremental fact rewrites no row.
- **Separate state.** Airflow writes its warehouse, extracts, exports and dbt logs inside its own volume, never into the project folder, so its results can be compared with `run-all`'s and the two never share files.

## What this is not

One container running the scheduler, DAG processor, API server and triggerer, with a local executor. It shows scheduling, intervals, ordering and backfill. It is not a production deployment: no workers, no high availability, no secrets backend, no monitoring.
