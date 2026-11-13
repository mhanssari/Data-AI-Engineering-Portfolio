"""CI check, run with the Airflow image's Python: the DAG imports, and its shape is what decision 0003 says.

No metadata database is needed: the DAG is parsed, not scheduled.
"""

import sys

from airflow.dag_processing.dagbag import DagBag

EXPECTED_ORDER = [
    "ensure_extracts",
    "load_batch",
    "source_freshness",
    "staging_and_snapshot",
    "marts",
    "export",
]

# The folder the shared platform mounts this case's DAGs at; CI passes the path explicitly.
DAG_FOLDER = sys.argv[1] if len(sys.argv) > 1 else "/opt/airflow/dags/retail_loyalty"
bag = DagBag(DAG_FOLDER)
if bag.import_errors:
    sys.exit(f"import errors: {bag.import_errors}")
dag = bag.dags.get("retail_loyalty")
if dag is None:
    sys.exit(f"retail_loyalty not found; parsed {sorted(bag.dag_ids)}")

order, task = [], dag.get_task("ensure_extracts")
while task is not None:
    order.append(task.task_id)
    downstream = list(task.downstream_list)
    task = downstream[0] if len(downstream) == 1 else None
problems = []
if order != EXPECTED_ORDER:
    problems.append(f"task chain {order}")
if dag.max_active_runs != 1:
    problems.append("max_active_runs must be 1: DuckDB allows one writer and batches must arrive in order")
if any("LOYALTY_WAREHOUSE" not in (t.env or {}) for t in dag.tasks):
    problems.append("every task must carry the case's own settings: the platform holds none")
if not all(t.depends_on_past for t in dag.tasks):
    problems.append("every task must depend on its past run, so batch 2 waits for batch 1")
if problems:
    sys.exit("; ".join(problems))
print(f"retail_loyalty: {len(dag.tasks)} tasks in order, one run at a time")
