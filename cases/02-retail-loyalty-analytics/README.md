# Retail loyalty and customer analytics

**Status: building.** Design agreed. Working and tested: the seeded generator, the append-only loader, dbt staging with source freshness, the member snapshot, the four dimensions, the sales facts (the incremental one proven equal to a full refresh), the loyalty facts with reward status resolved from the event log, the member-month and cohort marts with all ten business invariants tested, a recorded history of every test result, and the Parquet export. An opt-in Airflow DAG backfills both batches and gives the same marts as `run-all` ([evidence](evidence/airflow-backfill-run.md)). A Power BI report kept as text (PBIP/TMDL/PBIR): three pages with drill-through and region RLS, reconciling 34 of 34 checks to SQL ([evidence](evidence/power-bi-reconciliation.md)). CI is written and linted; its runs on GitHub have not been checked.

![Retention and cohorts](evidence/screenshots/report-executive.png)

A fictional retailer's marketing, store and finance teams count active customers, members and redemptions differently. This case builds one warehouse whose definitions make those numbers agree, and stay agreed when returns and corrections arrive late.

Stack: dbt-core on DuckDB by default, with Snowflake as an opt-in target not yet run ([decision 0001](docs/decisions/0001-warehouse-target.md)); an incremental fact, a dbt snapshot and history rebuilt from raw ([0002](docs/decisions/0002-history-strategy.md)); freshness, contracts and business-rule tests; Airflow as opt-in orchestration ([0003](docs/decisions/0003-orchestration.md)); Python; SQL; Power BI. No account, credentials or Docker on the default path.

## Start here

- [Requirements](docs/requirements.md): decisions supported, acceptance criteria, invariants
- [Data model](docs/data-model.md): sources, planted defects, model DAG, grain of every mart
- [Metric definitions](docs/metrics.md)
- [Architecture](docs/architecture.md)
- [Decisions](docs/decisions/README.md)

## Setup and execution

From this folder. Either way, versions come from `uv.lock`: Python 3.12, dbt-core 1.12.6, dbt-duckdb 1.11.1, DuckDB 1.5.6.

**In a Linux container** (Docker; [decision 0005](docs/decisions/0005-linux-container.md)):

```powershell
docker compose build
docker compose run --rm loyalty run-all                    # generate; per batch: load, freshness, dbt build; export
docker compose run --rm --entrypoint pytest loyalty -q
docker compose run --rm --entrypoint dbt loyalty build --project-dir dbt --profiles-dir dbt   # any dbt command
```

**Without Docker** (needs [uv](https://docs.astral.sh/uv/)):

```powershell
uv sync
uv run loyalty run-all
uv run pytest
```

`run-all` starts from an empty warehouse and takes about a minute. Single steps: `loyalty generate`, `loyalty load [--batch 1|2|all]`, `loyalty build`, `loyalty export`. The export writes every mart and the test history to `exports/*.parquet`, which Power BI reads. CI: [case-02-ci.yml](../../.github/workflows/case-02-ci.yml). Opt-in Airflow: [orchestration](orchestration/README.md).

[Return to portfolio](../../README.md).
