# Shared Airflow: late-file recovery and rerun (A6, A7)

Recorded 12 November 2026 on Windows with Docker Desktop, Airflow 3.4.0 (Python 3.12), LocalExecutor, PostgreSQL 18.6, seed 3303. This is a local demonstration, with no cloud deployment. Timings below are individual observations, not a benchmark.

## Setup and scope

Followed the [orchestration run guide](../orchestration/README.md), using the existing shared `portfolio-airflow` stack and the optional Case 03 Compose override. Case 03 is installed in `/opt/cases/payment-reconciliation`, separate from Airflow and Case 02. The base platform configuration also passed `docker compose ... config --quiet` without Case 03 credentials.

`prepare_a6.py` created a new `payments_airflow` database in Case 03's PostgreSQL service, generated the 31 synthetic arrival folders in the Airflow volume and loaded only arrivals through 21 October: 81 files. It did not reset or migrate the ordinary `payments` database or delete any shared volumes. The DAG loaded the next two arrivals itself.

## Scheduled backfill

Command, from the repository root:

```powershell
docker compose -f platform/airflow/compose.yaml exec airflow airflow backfill create `
    --dag-id payment_reconciliation --from-date 2026-10-22 --to-date 2026-10-23 `
    --max-active-runs 1 --reprocess-behavior failed
```

Both runs followed `wait_for_arrival -> load_arrival -> reconcile` and succeeded, in order:

| Logical date (UTC) | Run ID | Started (UTC, 12 November) | Ended (UTC) | State |
| --- | --- | --- | --- | --- |
| 22 October 2026 | `backfill__2026-10-22T00:00:00+00:00` | 14:12:46.940 | 14:13:08.498 | success |
| 23 October 2026 | `backfill__2026-10-23T00:00:00+00:00` | 14:13:09.267 | 14:13:29.985 | success |

The SQL verifier checked USD payments due on 20 October:

```text
2026-10-22: USD due 20 October: {'missing_settlement': 473, 'pending': 2}
2026-10-23: USD due 20 October: {'matched': 475}
Saved rerun baseline: {'ops.file': 90, 'raw.payment_event': 128820,
  'raw.settlement_line': 35670, 'raw.merchant': 41}; 1 successful execution(s)
A6 verified: P14 resolved, 7 planted exceptions remain, all control differences zero
```

The two still-pending payments on the 22nd have a later refund due date. The late file clears the missing settlements on the 23rd, leaving the seven deliberately planted exceptions. Every stored control total had `unexplained_minor = 0`.

## Cleared rerun

Saved the baseline with `verify_a6.py --save-rerun-baseline`, then cleared all three tasks for 23 October using `airflow tasks clear payment_reconciliation --start-date 2026-10-23 --end-date 2026-10-23 --yes`. The same DAG run succeeded again from 14:14:38.956 to 14:14:49.645 UTC.

`verify_a6.py --check-rerun` reported:

```text
2026-10-22: USD due 20 October: {'missing_settlement': 473, 'pending': 2}
2026-10-23: USD due 20 October: {'matched': 475}
Rerun: identical results and landing counts; executions 1 -> 2
A6 verified: P14 resolved, 7 planted exceptions remain, all control differences zero
```

The comparison covers ordered row digests of all four reconciliation result tables for the 23rd, plus row counts in `ops.file` and all three raw tables. The additional successful `ops.run` execution proves that the rerun executed.

## Other checks

- The shared image built successfully from the locked case dependencies.
- Case 03's DAG parsed without credentials or a metadata database: three sequential tasks and the 31-date UTC calendar. The readiness checks cover absent folders, a listed file missing, a complete delivery, wrong-date, empty and malformed manifests.
- Case 02's existing DAG check passed in the extended image: six ordered tasks, one active run.
- Both isolated CLIs passed the platform workflow's `--help` smoke checks.
- Case 03 `ruff check .` and `ruff format --check .` passed in its Linux container (45 Python files formatted).
- `actionlint` passed for the extended platform workflow. Its new Case 03 checks are configured locally; no GitHub workflow was triggered or verified in this slice.
- Documentation links and `git diff --check` passed. After validation, the Case 03 DAG was paused and the shared Airflow services were stopped. Their volumes and the demonstration database were preserved; the previously running Case 03 PostgreSQL service was left running.

An initial verifier invocation ran while the 23 October DAG was still running and correctly found no result for it. The verifier now reports an explicit wait instruction until both reconciliation executions have succeeded. The successful comparisons above ran after the DAGs finished.

## Limits

After this slice was committed as `ba8d9c9` and pushed to `case-03-payments`, the [Platform Airflow workflow](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43236947315) passed on GitHub (12 November 2026). It built the shared image, checked both DAGs and ran both CLI smoke checks. It did not execute the scheduler backfill on GitHub.

This ran two arrivals through the scheduler, with prior arrivals loaded by the CLI. It does not establish a full-month Airflow backfill. Missing-file behaviour was checked directly in the sensor callable; a live sensor timeout, a failed-task retry and concurrent-writer rejection were not injected. The existing transformation pytest suite was not rerun in this slice; the case logic was unchanged and the new integration was checked through the real DAG. Kafka, Spark and streaming recovery remain unimplemented.
