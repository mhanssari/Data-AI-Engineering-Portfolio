"""Parse the DAG with the platform's Python, without a metadata database or case credentials.

Check scheduling and missing-delivery behaviour as well as the import, so an early arrival cannot silently
trigger a load. Database transformation behaviour remains covered by the case's pytest suite.
"""

import importlib.util
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from airflow.dag_processing.dagbag import DagBag

folder = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/airflow/dags/payment_reconciliation")
bag = DagBag(str(folder))
assert not bag.import_errors, bag.import_errors
dag = bag.dags["payment_reconciliation"]
assert [task.task_id for task in dag.topological_sort()] == [
    "wait_for_arrival",
    "load_arrival",
    "reconcile",
]
assert dag.max_active_runs == 1 and dag.max_active_tasks == 1
assert all(task.depends_on_past for task in dag.tasks)
assert dag.get_task("wait_for_arrival").mode == "reschedule"
assert len(dag.timetable.event_dates) == 31
assert dag.timetable.event_dates[0].to_date_string() == "2026-10-02"
assert dag.timetable.event_dates[-1].to_date_string() == "2026-11-01"
for name in ("load_arrival", "reconcile"):
    task = dag.get_task(name)
    assert task.append_env and task.env == {"ARRIVAL": "{{ ds }}"}
    assert '"$ARRIVAL"' in task.bash_command
    assert "${CASE03_PGPASSWORD:?}" in task.bash_command

spec = importlib.util.spec_from_file_location("payment_dag_check", folder / "payment_reconciliation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with TemporaryDirectory() as tmp:
    assert not module.arrival_ready(tmp, "2026-10-22")
    arrival = Path(tmp) / "arrival_2026-10-22"
    arrival.mkdir()
    manifest = arrival / "manifest.json"
    manifest.write_text(json.dumps({"arrival_date": "2026-10-22", "files": [{"file": "events.csv"}]}))
    assert not module.arrival_ready(tmp, "2026-10-22")
    (arrival / "events.csv").write_text("ready")
    assert module.arrival_ready(tmp, "2026-10-22")
    manifest.write_text(json.dumps({"arrival_date": "2026-10-23", "files": [{"file": "events.csv"}]}))
    try:
        module.arrival_ready(tmp, "2026-10-22")
    except ValueError:
        pass
    else:
        raise AssertionError("wrong-date delivery was accepted")
    manifest.write_text(json.dumps({"arrival_date": "2026-10-22", "files": []}))
    try:
        module.arrival_ready(tmp, "2026-10-22")
    except ValueError:
        pass
    else:
        raise AssertionError("empty manifest was accepted")
    manifest.write_text("{")
    try:
        module.arrival_ready(tmp, "2026-10-22")
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("malformed manifest was accepted")
print("payment_reconciliation: 3 sequential tasks, 31 UTC arrivals; missing delivery waits, wrong date fails")
