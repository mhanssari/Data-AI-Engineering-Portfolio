# Airflow backfill run (acceptance A15)

Recorded 11 November 2026 on Windows 11 with Docker Desktop: Airflow 3.4.0 standalone (`apache/airflow:3.4.0-python3.12`), PostgreSQL 18.6 metadata database, LocalExecutor, seed 2602. A single run; timings are indicative only.

## Commands

From `orchestration/`, on fresh volumes (`docker compose down -v` first). This was the case's own Airflow stack at the time; it has since moved to the shared platform, re-run below:

```powershell
docker compose up -d --build
docker compose exec airflow airflow dags unpause retail_loyalty
docker compose exec airflow airflow backfill create --dag-id retail_loyalty `
    --from-date 2026-09-01 --to-date 2026-10-01 --max-active-runs 1 --reprocess-behavior failed
docker compose exec airflow bash /opt/loyalty-project/orchestration/verify_a15.sh   # path at the time
```

## Backfill

| Run | Logical date | State | Started | Ended |
| --- | --- | --- | --- | --- |
| `backfill__2026-09-16T00:00:00+00:00` | 2026-09-16 | success | 22:39:24 | 22:40:52 |
| `backfill__2026-09-30T00:00:00+00:00` | 2026-09-30 | success | 22:40:53 | 22:41:43 |

Batch 2's run started only after batch 1's ended.

## `verify_a15.sh` output

```text
== 1. Batches landed by the DAG, in load order
1 | 7 | 243320 | 2026-11-11 22:39:54
2 | 7 | 5898 | 2026-11-11 22:40:55
== 2. Airflow marts against loyalty run-all
batch 1: 243320 rows landed, 0 of 7 files already loaded
Done. PASS=105 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=105
batch 2: 5898 rows landed, 0 of 7 files already loaded
Done. PASS=105 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=105
agg_repeat_purchase_cohort: 0 differing rows
agg_retention_cohort: 0 differing rows
dim_date: 0 differing rows
dim_member: 0 differing rows
dim_product: 0 differing rows
dim_store: 0 differing rows
fct_loyalty_event: 0 differing rows
fct_member_month: 0 differing rows
fct_reward_entitlement: 0 differing rows
fct_sales_line: 0 differing rows
fct_transaction: 0 differing rows
== 3. Clear and rerun the 30 September interval
rerun state: success
(the same 11 marts: 0 differing rows each)
dbt runs that wrote fct_sales_line rows: 2 before, 2 after
dbt runs in the test history: 4 before, 6 after
1 | 7
2 | 7
A15 verified
```

The test history grew by two dbt runs, so the rerun did execute its staging and marts steps; it wrote nothing new.

## Problems found on the way

- **dbt could not write its log** in the mounted project folder, which another container's user had created. Airflow's dbt logs and compiled files now go to its own volume (`DBT_LOG_PATH`, `DBT_TARGET_PATH`).
- **SQLite locked** when the backfill command and the scheduler wrote at once. Replaced with PostgreSQL, reachable only inside the Compose network, so it needs no password.
- **Airflow 3 refuses a backfill** of a DAG with `depends_on_past` unless the reprocess behaviour is stated.

## Re-run on the shared Airflow, 12 November 2026

The Airflow stack moved from this case to the portfolio's [shared platform](../../../platform/airflow/README.md), with the case in its own environment, `/opt/cases/retail-loyalty`, and its settings carried by the DAG's tasks. A15 was re-run there, on fresh volumes:

```powershell
cd platform/airflow
docker compose up -d --build
docker compose exec airflow airflow dags unpause retail_loyalty
docker compose exec airflow airflow backfill create --dag-id retail_loyalty `
    --from-date 2026-09-01 --to-date 2026-10-01 --max-active-runs 1 --reprocess-behavior failed
docker compose exec airflow bash /opt/portfolio/cases/02-retail-loyalty-analytics/orchestration/verify_a15.sh
```

| Check | Result |
| --- | --- |
| DAG import errors | None |
| Backfill | 2 runs created; 16 September succeeded, then 30 September |
| Batches in load order | 1: 7 files, 243,320 rows; 2: 7 files, 5,898 rows |
| Marts against `run-all` | 0 differing rows in all 11 |
| Cleared rerun of 30 September | Succeeded; 0 differing rows; the same 2 dbt runs wrote the sales fact before and after; the test history grew from 4 runs to 6 |
| Files written into the project folder | None |

## Not validated

Airflow on GitHub (CI parses the DAG only), more than two deliveries, a failed task's retry, any production-like deployment.
