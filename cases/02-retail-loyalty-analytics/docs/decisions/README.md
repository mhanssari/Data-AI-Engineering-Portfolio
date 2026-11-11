# Architecture decisions

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-warehouse-target.md) | DuckDB by default, Snowflake as an opt-in target | Accepted |

Decisions still to record as the models are built: history from append-only raw instead of dbt snapshots; the incremental boundary for `fct_sales_line`; where metric logic lives between dbt and DAX.

Copy [the template](TEMPLATE.md) to a numbered descriptive file when a material choice is made. Status values: proposed, accepted or superseded.
