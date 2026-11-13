# Orchestration (opt-in)

Airflow runs the same steps as `loyalty run-all`, one DAG run per extract delivery ([decision 0003](../docs/decisions/0003-orchestration.md)). It runs in the portfolio's [shared Airflow](../../../platform/airflow/README.md); nothing else in the case needs it.

| File | Purpose |
| --- | --- |
| [dags/retail_loyalty.py](dags/retail_loyalty.py) | The DAG: extracts, load the run's batch, source freshness, staging and snapshot, marts, export. Each task calls the case's CLI in its own environment, with the case's settings |
| [check_dag.py](check_dag.py) | CI: the DAG imports and has the agreed shape, with no database |
| [verify_a15.sh](verify_a15.sh) | Acceptance A15: batches in order, marts equal to `run-all`, a rerun changes nothing |

## Run it

From `platform/airflow`:

```powershell
docker compose up -d --build
docker compose exec airflow airflow dags unpause retail_loyalty
docker compose exec airflow airflow backfill create --dag-id retail_loyalty `
    --from-date 2026-09-01 --to-date 2026-10-01 --max-active-runs 1 --reprocess-behavior failed
docker compose exec airflow bash /opt/portfolio/cases/02-retail-loyalty-analytics/orchestration/verify_a15.sh
```

## How it schedules

- **One run per delivery.** An `EventsTimetable` lists the two delivery dates, 16 and 30 September 2026. A run's logical date selects its batch, so a backfill over September creates exactly two runs.
- **In order.** `max_active_runs=1` and `depends_on_past` keep batch 2 behind batch 1. The snapshot needs this, and DuckDB allows one writer. Airflow 3 asks a backfill of such a DAG to say what to do with earlier runs; `--reprocess-behavior failed` reruns only failures.
- **Rerun safety.** Clearing an interval reloads nothing (checksums), the snapshot records nothing new, and the incremental fact rewrites no row.
- **Separate state.** The DAG's tasks keep the case's extracts, warehouse, exports and dbt artefacts in Airflow's volume, never in the project folder. Airflow's results can then be compared with `run-all`'s, and the platform holds no case settings.
