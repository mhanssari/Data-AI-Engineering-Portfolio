# dbt project

dbt Core 1.12 on DuckDB ([decision 0004](../docs/decisions/0004-dbt-core-v1.md)). The model DAG and the grain of every model are in the [data model](../docs/data-model.md).

| Folder | Contents |
| --- | --- |
| [models/staging](models/staging) | Sources with freshness thresholds, one view per source table: rename, cast, deduplicate, no joins |
| [snapshots](snapshots) | `snp_member`: what was reported before a member was corrected ([decision 0002](../docs/decisions/0002-history-strategy.md)) |
| [macros](macros) | `generate_schema_name`: schemas named `staging`, `snapshots`, `audit` as configured |

Schemas in the warehouse: `raw` (loader), `staging`, `snapshots`, and `audit`, where every failing test stores its failing rows.

## Running dbt directly

The `loyalty` CLI runs dbt for you. To run it by hand, from this folder:

```powershell
Copy-Item profiles.example.yml profiles.yml   # once; the DuckDB profile has no secrets
uv run dbt build
uv run dbt source freshness
```

The profile reads `LOYALTY_WAREHOUSE` and defaults to `../warehouse/loyalty.duckdb`. Usage statistics are switched off in `dbt_project.yml`.
