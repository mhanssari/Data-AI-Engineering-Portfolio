# Case 03 on the shared Airflow

The [DAG](dags/payment_reconciliation.py) runs `wait_for_arrival -> load_arrival -> reconcile`, one run per UTC arrival date from 2 October to 1 November 2026. An `EventsTimetable` lists the generator's finite delivery calendar. `max_active_runs=1`, `max_active_tasks=1` and `depends_on_past` keep runs and tasks sequential because core represents one arrival date at a time.

The sensor waits for the manifest and every listed file, rescheduling every 30 seconds for up to 10 minutes. Invalid manifest dates or empty manifests fail. The loader then checks checksums, headers and rows. A malformed record is quarantined; a rejected arrival or a failed reconciliation check fails the task. Load and reconciliation each retry once after 15 seconds, relying on transactional landing and rerun safety. These are bounded local-demo settings, not a production SLA.

## Configuration

This case uses the [portfolio Airflow](../../../platform/airflow/README.md). The optional [Compose override](compose.airflow.yaml) supplies Case 03 settings to that same service; it creates no second scheduler. It reads the case's ignored `.env`, passes namespaced `CASE03_PG*` variables, and defaults to a separate `payments_airflow` database. Set `PAYRECON_AIRFLOW_DB` in the case `.env` to select another database. Credentials are expanded only inside executing tasks; they are never read while parsing or stored in an operator's `env`.

Case code is mounted under `/opt/portfolio`; its locked Python environment is `/opt/cases/payment-reconciliation`. Demo source files and the rerun baseline live under `/opt/airflow/payment_reconciliation` in the platform volume. PostgreSQL stays in the case's Compose project, reached through `host.docker.internal` and its published port. Do not run another writer, CLI replay or backfill against the same database concurrently.

## Run the late-file demonstration (A6)

Docker and a case `.env` are required. All commands below run from the repository root. The existing case database is preserved: preparation creates a separate database and refuses to overwrite one that already exists. If the demonstration is already prepared, skip preparation and use task clearing to rerun it.

```powershell
docker compose -f cases/03-payment-reconciliation/compose.yaml up -d --wait postgres
docker compose --env-file cases/03-payment-reconciliation/.env `
    -f platform/airflow/compose.yaml `
    -f cases/03-payment-reconciliation/orchestration/compose.airflow.yaml up -d --build

# Generate the synthetic deliveries, migrate the separate database and load only arrivals through 21 October.
docker compose -f platform/airflow/compose.yaml exec airflow /opt/cases/payment-reconciliation/bin/python `
    /opt/portfolio/cases/03-payment-reconciliation/orchestration/prepare_a6.py

docker compose -f platform/airflow/compose.yaml exec airflow airflow dags unpause payment_reconciliation
docker compose -f platform/airflow/compose.yaml exec airflow airflow backfill create `
    --dag-id payment_reconciliation --from-date 2026-10-22 --to-date 2026-10-23 `
    --max-active-runs 1 --reprocess-behavior failed
docker compose -f platform/airflow/compose.yaml exec airflow airflow dags list-runs payment_reconciliation
```

Wait for both backfill runs to succeed in the command output or the local Airflow UI at `http://127.0.0.1:8080`. The CLI creates the backfill; it does not wait for completion. Then:

```powershell
docker compose -f platform/airflow/compose.yaml exec airflow /opt/cases/payment-reconciliation/bin/python `
    /opt/portfolio/cases/03-payment-reconciliation/orchestration/verify_a6.py --save-rerun-baseline
docker compose -f platform/airflow/compose.yaml exec airflow airflow tasks clear payment_reconciliation `
    --start-date 2026-10-23 --end-date 2026-10-23 --yes
# Wait for 23 October to succeed again, then compare results and confirm another execution was logged.
docker compose -f platform/airflow/compose.yaml exec airflow /opt/cases/payment-reconciliation/bin/python `
    /opt/portfolio/cases/03-payment-reconciliation/orchestration/verify_a6.py --check-rerun
```

The verifier checks 473 missing and 2 pending USD payments on 22 October, all 475 matched on 23 October, seven remaining planted exceptions, zero unexplained control differences, and unchanged landing counts and result digests on the cleared rerun. This demonstrates the late-file recovery on two scheduled arrivals; it is not a full-month Airflow backfill. The full-month batch replay is separately documented in the case README.

## Checks and shutdown

The [DAG check](check_dag.py) runs in the shared image without a database or case credentials. The platform workflow parses both cases' DAGs and checks both isolated CLIs. Case 03's database test workflow is the next slice.

```powershell
docker compose -f platform/airflow/compose.yaml exec airflow python `
    /opt/portfolio/cases/03-payment-reconciliation/orchestration/check_dag.py
docker compose -f platform/airflow/compose.yaml exec airflow airflow dags pause payment_reconciliation
docker compose -f platform/airflow/compose.yaml stop
```

Stopping the shared platform affects all its DAGs. It preserves volumes and the demonstration database. Case cleanup must not delete the platform's volumes; infrastructure lifecycle follows [portfolio decision 0001](../../../platform/decisions/0001-shared-infrastructure.md).
