# 0003 Orchestration: one CLI by default, Airflow opt-in

Status: accepted

Date: 11 November 2026

## Context

The pipeline is linear: generate, load a batch, snapshot, build, export. It must run from a clean clone without Docker. Scheduling, per-interval runs and backfill are worth showing, and Airflow is the scheduler most teams run.

## Options

1. **CLI only.** Simplest. Shows nothing about scheduling, intervals or backfill.
2. **Airflow only.** Shows orchestration, but makes Docker and several gigabytes of memory a prerequisite for anyone who wants to run the case.
3. **CLI by default, Airflow opt-in, both calling the same steps.** The default path stays light; Airflow adds intervals and backfill without owning any logic.

## Decision and rationale

Option 3. The steps live in the case's Python package and dbt project. The CLI calls them in order; the Airflow DAG calls the same steps, one run per batch interval, with dbt invoked per layer. Neither contains logic the other lacks.

## Consequences and validation

- Airflow is described as working knowledge in the profile. This case shows a DAG that runs and backfills locally, not production operation.
- DuckDB allows one writer, so tasks run one at a time.
- CI checks that the DAG imports; acceptance A15 records a run, a two-interval backfill and an idempotent rerun, with marts compared against the CLI run.
- Cosmos (one task per dbt model) is not used; it could replace the per-layer tasks if model-level retries ever mattered.
