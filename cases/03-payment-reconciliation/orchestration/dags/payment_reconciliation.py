"""One run per synthetic arrival, using the portfolio's shared Airflow.

The producer prepares arrival folders separately. The sensor waits for the manifest and every listed file;
the loader owns checksum, schema and row validation. Each batch task calls the case's isolated CLI.
Credentials are inherited at task execution from the optional case Compose override, never read at parse time
or placed in an operator's serialised env. Run only one writer against this case database at a time.
"""

import json
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.sensors.python import PythonSensor
from airflow.sdk import DAG, chain
from airflow.timetables.events import EventsTimetable

PAYRECON = "/opt/cases/payment-reconciliation/bin/payrecon"
DATA = "/opt/airflow/payment_reconciliation/data"
ARRIVALS = [pendulum.datetime(2026, 10, 2, tz="UTC").add(days=n) for n in range(31)]

# Bash logs these variable references, not their expanded values. Never enable shell tracing here.
CONNECTION_ENV = """export PGHOST="${CASE03_PGHOST:?Case 03 Airflow settings are missing}"
export PGPORT="${CASE03_PGPORT:?}" PGDATABASE="${CASE03_PGDATABASE:?}"
export PGUSER="${CASE03_PGUSER:?}" PGPASSWORD="${CASE03_PGPASSWORD:?}"
export PGCONNECT_TIMEOUT=10
"""


def arrival_ready(data: str, arrival: str) -> bool:
    """A missing delivery waits; a malformed manifest fails visibly rather than waiting indefinitely."""
    folder = Path(data) / f"arrival_{arrival}"
    manifest = folder / "manifest.json"
    if not manifest.is_file():
        return False
    contents = json.loads(manifest.read_text(encoding="utf-8"))
    if contents["arrival_date"] != arrival or not contents["files"]:
        raise ValueError(f"{manifest}: wrong arrival date or empty file list")
    return all((folder / entry["file"]).is_file() for entry in contents["files"])


with DAG(
    dag_id="payment_reconciliation",
    description="Wait for one arrival, load it safely, reconcile with financial control checks",
    schedule=EventsTimetable(
        event_dates=ARRIVALS,
        restrict_to_events=True,
        description="Daily arrivals, 2 October to 1 November 2026 (UTC)",
    ),
    start_date=ARRIVALS[0],
    catchup=False,
    is_paused_upon_creation=True,
    max_active_runs=1,
    max_active_tasks=1,
    default_args={"depends_on_past": True, "retries": 1, "retry_delay": timedelta(seconds=15)},
    tags=["case-03", "batch"],
) as dag:
    wait_for_arrival = PythonSensor(
        task_id="wait_for_arrival",
        python_callable=arrival_ready,
        op_kwargs={"data": DATA, "arrival": "{{ ds }}"},
        mode="reschedule",
        poke_interval=30,
        timeout=600,
        retries=0,
    )
    load_arrival = BashOperator(
        task_id="load_arrival",
        bash_command=CONNECTION_ENV + f'exec {PAYRECON} load --data "{DATA}" --arrival "$ARRIVAL"',
        env={"ARRIVAL": "{{ ds }}"},
        append_env=True,
        execution_timeout=timedelta(minutes=5),
    )
    reconcile = BashOperator(
        task_id="reconcile",
        bash_command=CONNECTION_ENV + f'exec {PAYRECON} reconcile --arrival "$ARRIVAL"',
        env={"ARRIVAL": "{{ ds }}"},
        append_env=True,
        execution_timeout=timedelta(minutes=5),
    )

    chain(wait_for_arrival, load_arrival, reconcile)
