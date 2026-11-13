"""Opt-in orchestration of the same steps `loyalty run-all` performs (decision 0003).

One DAG run per extract delivery. The source system delivers two extracts, on 16 and 30 September 2026; an
EventsTimetable schedules a run on each, and the run's logical date picks its batch. A backfill over September
therefore replays batch 1, then batch 2, in order:

    airflow backfill create --dag-id retail_loyalty --from-date 2026-09-01 --to-date 2026-10-01

Order matters, because the member snapshot records state as of each batch: max_active_runs=1 and depends_on_past
keep batch 2 behind batch 1. Every task calls the case's CLI in its own environment, so the DAG holds no logic of
its own. DuckDB allows one writer, so the tasks run one after another.

Runs in the shared portfolio Airflow (platform/airflow). The case's settings travel with its tasks, not with the
platform: Airflow's runs keep their extracts, warehouse, exports and dbt artefacts inside Airflow's volume, apart
from the project folder, so they can be compared with `loyalty run-all`.
"""

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, chain
from airflow.timetables.events import EventsTimetable

# Delivery date -> batch. A third delivery would be one more entry here and in the generator.
BATCH_DELIVERIES = {"2026-09-16": 1, "2026-09-30": 2}
LOYALTY = "/opt/cases/retail-loyalty/bin/loyalty"
STATE = "/opt/airflow/loyalty"
CASE_ENV = {
    "LOYALTY_DATA_DIR": f"{STATE}/data",
    "LOYALTY_WAREHOUSE": f"{STATE}/warehouse/loyalty.duckdb",
    "LOYALTY_EXPORT_DIR": f"{STATE}/exports",
    "DBT_LOG_PATH": f"{STATE}/dbt/logs",
    "DBT_TARGET_PATH": f"{STATE}/dbt/target",
}


def batch_for(ds: str) -> int:
    return BATCH_DELIVERIES[ds]


with DAG(
    dag_id="retail_loyalty",
    description="Load one extract delivery, test each layer, build the marts, export for Power BI",
    schedule=EventsTimetable(
        event_dates=[pendulum.parse(day, tz="UTC") for day in BATCH_DELIVERIES],
        restrict_to_events=True,
        description="On each extract delivery",
    ),
    start_date=pendulum.datetime(2026, 9, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"depends_on_past": True, "retries": 0, "env": CASE_ENV, "append_env": True},
    user_defined_macros={"batch_for": batch_for},
    tags=["case-02"],
) as dag:
    # Stands in for the source system: writes the extracts once, then finds them already delivered.
    ensure_extracts = BashOperator(task_id="ensure_extracts", bash_command=f"{LOYALTY} generate --if-missing")

    # Idempotent: a batch whose files are already loaded lands nothing.
    load_batch = BashOperator(
        task_id="load_batch", bash_command=f"{LOYALTY} load --batch {{{{ batch_for(ds) }}}}"
    )

    source_freshness = BashOperator(task_id="source_freshness", bash_command=f"{LOYALTY} freshness")

    # Each layer is built and tested before the next runs. Cautious selection keeps tests that also read the
    # marts out of this step, so they never run against marts from the previous batch.
    staging_and_snapshot = BashOperator(
        task_id="staging_and_snapshot",
        bash_command=f"{LOYALTY} build --select path:models/staging path:snapshots --indirect-selection cautious",
    )

    marts = BashOperator(
        task_id="marts",
        bash_command=f"{LOYALTY} build --select path:models/intermediate path:models/marts",
    )

    export = BashOperator(task_id="export", bash_command=f"{LOYALTY} export")

    chain(ensure_extracts, load_batch, source_freshness, staging_and_snapshot, marts, export)
