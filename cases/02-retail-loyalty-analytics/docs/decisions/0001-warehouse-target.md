# 0001 Warehouse target: DuckDB by default, Snowflake opt-in

Status: accepted

Date: 11 November 2026

## Context

The original plan named Snowflake. A Snowflake run needs an account and credentials, so a reviewer cannot clone and run the case, and CI would need secrets. The modelling this case is meant to show (grain, history, incremental correctness, metric definitions) does not depend on the warehouse.

## Options

1. **Snowflake only.** The most realistic target for many analytics engineering teams. Nobody can run it without an account; CI needs stored credentials; a trial expires.
2. **DuckDB only.** Runs anywhere with no setup. Says nothing about portability, and some reviewers will discount a local engine.
3. **DuckDB by default, Snowflake as an opt-in dbt target.** The default path runs anywhere; portability is designed in through dispatched macros and shown only once actually run.

## Decision and rationale

Option 3. The default path, CI and every result in the README use DuckDB. Snowflake is a second target in `profiles.example.yml`, built only after the DuckDB path is complete.

## Consequences and validation

- The case does not claim Snowflake execution until a run is recorded in `evidence/`, with the account edition, warehouse size and cost.
- Warehouse-specific tuning (clustering, warehouse sizing, query profile) is discussed in prose, not demonstrated.
- Models must avoid DuckDB-only syntax, or put it behind a dispatched macro. A Snowflake run is the only real check of that; until then it is a design intention.
- The folder was renamed from `02-loyalty-dbt-snowflake` to `02-retail-loyalty-analytics`, so the name describes the problem and does not claim a platform.
