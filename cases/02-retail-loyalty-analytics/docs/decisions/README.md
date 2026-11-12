# Architecture decisions

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-warehouse-target.md) | DuckDB by default, Snowflake as an opt-in target | Accepted |
| [0002](0002-history-strategy.md) | Rebuild store versions from raw; snapshot members | Accepted |
| [0003](0003-orchestration.md) | One CLI by default, Airflow opt-in | Accepted |
| [0004](0004-dbt-core-v1.md) | dbt Core v1, kept ready for v2 | Accepted |
| [0005](0005-linux-container.md) | Run in a Linux container; keep a local path without Docker | Accepted |
| [0006](0006-incremental-boundary.md) | The incremental boundary of `fct_sales_line` | Accepted |
| [0007](0007-metric-logic-dbt-and-dax.md) | Where metric logic lives: dbt for rules, DAX for aggregation | Accepted |

Copy [the template](TEMPLATE.md) to a numbered descriptive file when a material choice is made. Status values: proposed, accepted or superseded.
