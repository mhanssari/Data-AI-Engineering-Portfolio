# Retail loyalty and customer analytics

**Status: building.** Design agreed; the seeded generator and the append-only loader work and are tested. The dbt models, report and orchestration are not built yet.

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
uv run loyalty generate # both batch extracts into data/generated/
uv run loyalty load     # land them in warehouse/loyalty.duckdb; running it again lands nothing
uv run pytest
```

The dbt models come next; there is nothing to build yet.

[Return to portfolio](../../README.md).
