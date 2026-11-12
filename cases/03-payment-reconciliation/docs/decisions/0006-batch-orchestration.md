# 0006 Bounded arrival scheduling on the shared Airflow

Status: accepted

Date: 12 November 2026

## Context

Milestone 1 needs a repeatable daily settlement pipeline and a late-file recovery demonstration. Core is rebuilt for one arrival date at a time (decision 0004), while the synthetic producer has a finite calendar. Shared infrastructure ownership follows [portfolio decision 0001](../../../../platform/decisions/0001-shared-infrastructure.md).

## Options

1. An unbounded daily cron schedule, with interval-to-arrival mapping and an end date.
2. An `EventsTimetable` listing the generator's 31 UTC arrival dates, as in Case 02.

## Decision and rationale

Option 2. Each logical date is exactly the arrival date passed to the CLI; there is no midnight interval offset to infer. This is a bounded synthetic replay, not a continuously operating production schedule.

- Tasks: `wait_for_arrival -> load_arrival -> reconcile`. Generate data and apply migrations separately, before scheduling.
- The sensor waits for a manifest and all listed files, rescheduling every 30 seconds with a ten-minute timeout. The loader owns checksum, schema and row validation. A wrong-date or empty manifest fails the sensor visibly.
- One active run and one active task; tasks depend on their preceding run. Backfills must also set `--max-active-runs 1`. No other writer may target the same database during this demo.
- Load and reconciliation retry once after 15 seconds; their transactions and idempotency make retry safe. The sensor has no retry after its timeout. These bounds are reversible local-demo defaults.
- Install the case in `/opt/cases/payment-reconciliation`, isolated from Airflow. A case-owned optional Compose override adds `CASE03_PG*` settings to the existing platform service. This avoids requiring Case 03 credentials when starting the base platform for another case.
- Pass credentials from the task process environment at execution, never through parse-time reads or templated operator environment fields. Shell tracing must stay disabled.
- Use a separate `payments_airflow*` database for acceptance evidence. Preparation refuses to replace an existing database. The ordinary case database and Airflow metadata are preserved.

## Consequences and validation

The [run guide](../../orchestration/README.md) owns commands and operating details. The DAG check covers the delivery calendar, task ordering and missing/wrong-date manifests without a database. The acceptance verifier checks late-file recovery and unchanged results on a cleared rerun. Runtime evidence is recorded in the case's evidence folder; passing local checks does not establish GitHub CI success.

At a production handoff, revisit scheduling, file publication atomicity, sensor timeout, retries, credential management and concurrent-writer enforcement. Those are outside this local slice.
