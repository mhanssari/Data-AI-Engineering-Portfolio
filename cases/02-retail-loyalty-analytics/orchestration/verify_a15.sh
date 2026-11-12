#!/usr/bin/env bash
# Acceptance A15, run inside the Airflow container after the backfill has finished:
#   docker compose exec airflow bash /opt/portfolio/cases/02-retail-loyalty-analytics/orchestration/verify_a15.sh
# (from platform/airflow; the shared portfolio Airflow)
#
# 1. The DAG loaded batch 1, then batch 2.
# 2. Its marts equal those of `loyalty run-all`, built separately into another warehouse.
# 3. Clearing and rerunning the 30 September interval changes no mart and rewrites no row.
set -euo pipefail

LOYALTY=/opt/cases/retail-loyalty/bin/loyalty
PYTHON=/opt/cases/retail-loyalty/bin/python
BASE=/opt/airflow/loyalty
WAREHOUSE=$BASE/warehouse/loyalty.duckdb
REFERENCE=$BASE/reference
BEFORE=$BASE/before-rerun
# dbt's logs and compiled files stay in Airflow's volume, never in the mounted project folder.
export DBT_LOG_PATH=$BASE/reference/dbt/logs DBT_TARGET_PATH=$BASE/reference/dbt/target

query() {
    "$PYTHON" - "$1" "$2" <<'PY'
import sys, duckdb
con = duckdb.connect(sys.argv[1], read_only=True)
for row in con.execute(sys.argv[2]).fetchall():
    print(*row, sep=" | ")
PY
}

echo "== 1. Batches landed by the DAG, in load order"
query "$WAREHOUSE" "select batch_id, count(*) as files, sum(row_count) as rows, min(loaded_at) from raw._load_log group by 1 order by min(load_id)"

echo "== 2. Airflow marts against loyalty run-all"
rm -rf "$REFERENCE" && mkdir -p "$REFERENCE"
"$LOYALTY" run-all --data "$REFERENCE/data" --warehouse "$REFERENCE/warehouse/loyalty.duckdb" \
    --out "$REFERENCE/exports" > "$REFERENCE/run-all.log" 2>&1
grep -E "rows landed|Done. PASS" "$REFERENCE/run-all.log"
"$LOYALTY" compare "$WAREHOUSE" "$REFERENCE/warehouse/loyalty.duckdb"

echo "== 3. Clear and rerun the 30 September interval"
rm -rf "$BEFORE" && mkdir -p "$BEFORE" && cp "$WAREHOUSE" "$BEFORE/loyalty.duckdb"
writers_before=$(query "$WAREHOUSE" "select count(distinct built_in_invocation_id) from marts.fct_sales_line")
runs_before=$(query "$WAREHOUSE" "select count(distinct invocation_id) from audit.dq_test_result")
airflow tasks clear retail_loyalty --start-date 2026-09-30 --end-date 2026-09-30 --yes > /dev/null
for _ in $(seq 1 60); do
    sleep 10
    state=$(airflow dags list-runs retail_loyalty -o plain 2>/dev/null | awk '/2026-09-30T00:00:00/ {print $3; exit}')
    [[ "$state" == "success" || "$state" == "failed" ]] && break
done
echo "rerun state: $state"
[[ "$state" == "success" ]]
"$LOYALTY" compare "$BEFORE/loyalty.duckdb" "$WAREHOUSE"
writers_after=$(query "$WAREHOUSE" "select count(distinct built_in_invocation_id) from marts.fct_sales_line")
runs_after=$(query "$WAREHOUSE" "select count(distinct invocation_id) from audit.dq_test_result")
echo "dbt runs that wrote fct_sales_line rows: $writers_before before, $writers_after after"
echo "dbt runs in the test history: $runs_before before, $runs_after after"
query "$WAREHOUSE" "select batch_id, count(*) as files from raw._load_log group by 1 order by 1"
[[ "$writers_before" == "$writers_after" ]]
echo "A15 verified"
