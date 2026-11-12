# Run record

Final verification, 12 November 2026, Windows 11 with Docker Desktop 29.9. Seed 2602. Versions from `uv.lock`: Python 3.12, dbt-core 1.12.6, dbt-duckdb 1.11.1, DuckDB 1.5.6. Single runs; timings are indicative only.

## In the Linux container

```powershell
docker compose run --rm --entrypoint dbt loyalty parse --project-dir dbt --profiles-dir dbt --no-partial-parse --warn-error-options '{"error": ["Deprecations"]}'
docker compose run --rm loyalty run-all
docker compose run --rm loyalty reference
docker compose run --rm --entrypoint pytest loyalty -q
docker compose run --rm --entrypoint ruff loyalty check .
docker compose run --rm --entrypoint ruff loyalty format --check .
```

| Step | Result |
| --- | --- |
| dbt parse, deprecations as errors | Exit 0, no deprecation |
| `run-all` from an empty warehouse | Exit 0 in 108 s. Batch 1: 243,320 rows landed, 7 of 7 sources fresh, `dbt build` 106 of 106, no warnings. Batch 2: 5,898 rows, 7 of 7 fresh, 106 of 106. 12 Parquet files exported |
| pytest | 57 passed in 260 s; the D7 test added afterwards also passed (58 in total) |
| ruff | Check and format clean |

Earlier `run-all` runs took 37–56 s. Time varies with the machine's other load.

## A breaking change is rejected (A11)

`is_current` in `dim_store` was changed from a boolean to `'Y'`/`'N'`, and dbt was run on the changed model, then the file was restored.

- With unit tests included, the unit test `dim_store_chains_versions_without_gaps` failed first. The model was never built.
- With unit tests excluded, the enforced contract refused the model:

```text
Compilation Error in model dim_store (models/marts/dim_store.sql)
  This model has an enforced contract that failed.
  | column_name | definition_type | contract_type | mismatch_reason    |
  | is_current  | VARCHAR         | BOOLEAN       | data type mismatch |
Done. PASS=2 WARN=0 ERROR=1 SKIP=8 NO-OP=0 REUSED=0 TOTAL=11
```

In CI, either failure stops the pull request's checks before merge.

## From a clean clone

A fresh clone of the pushed branch (`3611f34`) from GitHub into an empty folder, run on Windows with uv only: no Docker, nothing from the working copy.

```powershell
git clone --branch case-02-retail-loyalty https://github.com/<owner>/<repository>.git
cd <repository>/cases/02-retail-loyalty-analytics
uv sync --frozen
uv run loyalty run-all
uv run pytest -q
```

| Step | Result |
| --- | --- |
| `uv sync --frozen` | 12 s; Python 3.12.16, dbt-core 1.12.6, DuckDB 1.5.6 |
| `run-all` | Exit 0 in 91 s; 243,320 + 5,898 rows landed; `dbt build` 106 of 106 for each batch; 12 Parquet files |
| pytest | 57 passed in 213 s, every test in that commit |

## Mutation checks

Throwaway scripts, not in the repository, broke the logic and confirmed the tests failed. Files were restored after each.

| Area | Caught |
| --- | --- |
| Staging deduplication, corrections, snapshot | 5 of 5 |
| Dimensions: version chaining, inferred products, as-of date, day of week, month completeness | 6 of 6 |
| Sales facts: watermark, dependants, store repair, attribution, discount sign, over-return order, unmatched returns, member flag | 8 of 8 |
| Loyalty marts: event log against status column, expiry, conflicts, denominator, events joined before aggregating | 6 of 6 |
| Member and cohort marts | 7 of 7, after adding a unit test for same-day repeats, which the first pass missed |
| Test history hook | 2 of 2 |
| Airflow DAG shape check | 1 of 1 |

## Other records

- [Power BI reconciliation](power-bi-reconciliation.md): 34 of 34.
- [Airflow backfill run](airflow-backfill-run.md): marts equal to `run-all`; a rerun changed nothing.

## Not validated

The Snowflake target; the Power BI service; Linux hosts other than the container; anything at a larger volume.
