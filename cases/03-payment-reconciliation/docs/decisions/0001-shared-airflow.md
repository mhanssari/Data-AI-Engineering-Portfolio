# 0001 One shared Airflow for the portfolio

Status: accepted

Date: 12 November 2026

## Context

Case 02 already has a working Airflow 3.4 stack (standalone, PostgreSQL metadata, the case installed in its own environment), proven by a recorded backfill. Case 03 needs Airflow for daily settlement runs. The portfolio rule is that each case runs on its own; a shared service must not become a dependency of a case's default path.

## Options

1. **A copy per case.** Fully independent, but two copies of the same infrastructure to maintain, and only one fits in memory at a time.
2. **Mount case 03 into case 02's stack.** Least work, but case 03 would depend on case 02's folder.
3. **One portfolio-level Airflow** in `platform/airflow/` that mounts each case's DAGs and environment. Every case keeps its own CLI path that needs no Airflow.

## Decision and rationale

Option 3, chosen by the owner. It is how a team would run it: one scheduler, many pipelines, each owned by its project.

## Consequences and validation

- Case 02's orchestration moves to `platform/airflow/`. Its acceptance A15, the backfill matching `run-all`, must be re-run on the shared stack and its evidence updated before case 03 adds a DAG.
- The image installs each case's environment separately, under `/opt/cases/<case>`, so dbt and PySpark versions never meet Airflow's.
- Case services join an external Docker network, `portfolio`. Credentials stay in each case's `.env`, which is never committed.
- CI checks every case's DAGs in the shared image.
