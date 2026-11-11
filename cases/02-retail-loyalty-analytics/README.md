# Retail loyalty and customer analytics

**Status: building.** Design agreed. Working and tested: the seeded generator, the append-only loader, dbt staging with source freshness, and the member snapshot. Not built yet: intermediate and mart models, business-rule tests, CI, Airflow, the Power BI report.

A fictional retailer's marketing, store and finance teams count active customers, members and redemptions differently. This case builds one warehouse whose definitions make those numbers agree, and stay agreed when returns and corrections arrive late.

Stack: dbt-core on DuckDB by default, with Snowflake as an opt-in target not yet run ([decision 0001](docs/decisions/0001-warehouse-target.md)); an incremental fact, a dbt snapshot and history rebuilt from raw ([0002](docs/decisions/0002-history-strategy.md)); freshness, contracts and business-rule tests; Airflow as opt-in orchestration ([0003](docs/decisions/0003-orchestration.md)); Python; SQL; Power BI. No account, credentials or Docker on the default path.

## Start here

- [Requirements](docs/requirements.md): decisions supported, acceptance criteria, invariants
- [Data model](docs/data-model.md): sources, planted defects, model DAG, grain of every mart
- [Metric definitions](docs/metrics.md)
- [Architecture](docs/architecture.md)
- [Decisions](docs/decisions/README.md)

## Setup and execution

Needs [uv](https://docs.astral.sh/uv/). From this folder, PowerShell or any shell:

```powershell
uv sync                 # Python 3.12, dbt-core 1.12.6, dbt-duckdb 1.11.1, DuckDB 1.5.6, pinned in uv.lock
uv run loyalty run-all  # from an empty warehouse: generate, then per batch load, freshness, dbt build
uv run pytest
```

`run-all` takes about 40 seconds. Single steps: `loyalty generate`, `loyalty load [--batch 1|2|all]`, `loyalty build`. So far the build covers staging, the member snapshot and their tests; marts come next.

[Return to portfolio](../../README.md).
