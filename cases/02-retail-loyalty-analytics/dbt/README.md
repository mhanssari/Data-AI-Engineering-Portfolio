# dbt project

dbt Core 1.12 on DuckDB ([decision 0004](../docs/decisions/0004-dbt-core-v1.md)). The model DAG and the grain of every model are in the [data model](../docs/data-model.md).

| Folder | Contents |
| --- | --- |
| [models/staging](models/staging) | Sources with freshness thresholds, one view per source table: rename, cast, deduplicate, no joins |
| [models/intermediate](models/intermediate) | Ephemeral business logic; `int_as_of_date` |
| [models/marts](models/marts) | Dimensions (date, store versions, product with inferred rows, member) sales facts (`fct_sales_line`, incremental; `fct_transaction`) loyalty facts (`fct_loyalty_event`, `fct_reward_entitlement`), `fct_member_month`, and the cohort aggregates (`agg_repeat_purchase_cohort`, `agg_retention_cohort`). Contracts enforced; dbt unit tests in the YAML |
| [snapshots](snapshots) | `snp_member`: what was reported before a member was corrected ([decision 0002](../docs/decisions/0002-history-strategy.md)) |
| [tests](tests) | Singular tests, one per business invariant, named `assert_<what must be true>` |
| [macros](macros) | `generate_schema_name` (schemas named as configured); `iso_day_of_week`, dispatched per adapter because engines spell it differently |

Schemas in the warehouse: `raw` (loader), `staging`, `marts`, `snapshots`, and `audit`, where every failing test stores its failing rows.

## Running dbt directly

The `loyalty` CLI runs dbt for you. To run it by hand, from this folder:

```powershell
Copy-Item profiles.example.yml profiles.yml   # once; the DuckDB profile has no secrets
uv run dbt build
uv run dbt source freshness
```

The profile reads `LOYALTY_WAREHOUSE` and defaults to `../warehouse/loyalty.duckdb`. Usage statistics are switched off in `dbt_project.yml`.
