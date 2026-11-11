# Retail loyalty and customer analytics

**Status: building.** Design agreed. Working and tested: the seeded generator, the append-only loader, dbt staging with source freshness, the member snapshot, and the four dimensions. Not built yet: facts, cohort and redemption marts, most business-rule tests, CI, Airflow, the Power BI report.

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
docker compose run --rm loyalty run-all                    # generate, then per batch: load, freshness, dbt build
docker compose run --rm --entrypoint pytest loyalty -q
docker compose run --rm --entrypoint dbt loyalty build --project-dir dbt --profiles-dir dbt   # any dbt command
```

**Without Docker** (needs [uv](https://docs.astral.sh/uv/)):

```powershell
uv sync
uv run loyalty run-all
uv run pytest
```

`run-all` starts from an empty warehouse and takes under a minute. Single steps: `loyalty generate`, `loyalty load [--batch 1|2|all]`, `loyalty build`. So far the build covers staging, the member snapshot, the dimensions and their tests; facts come next.

[Return to portfolio](../../README.md).
