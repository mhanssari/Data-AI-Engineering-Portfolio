# Runbook

Every command here was run on Windows 11 on 11–12 November 2026, from the case folder unless stated. In the container, prefix a `loyalty` command with `docker compose run --rm loyalty`; without Docker, prefix it with `uv run`.

## Set up

| Path | Commands |
| --- | --- |
| Container (default for development) | `docker compose build` |
| Local Python | Install [uv](https://docs.astral.sh/uv/), then `uv sync` |

Nothing needs a password or an account. `dbt/profiles.yml` is created from `profiles.example.yml` on first use.

## Run

| Step | Command | Notes |
| --- | --- | --- |
| Everything, from an empty warehouse | `loyalty run-all` | Generate; per batch: load, source freshness, `dbt build`; export. Under two minutes |
| Generate extracts | `loyalty generate [--seed N] [--if-missing]` | Same seed, same bytes |
| Load | `loyalty load [--batch 1\|2\|all]` | Batches must arrive in order; reloading lands nothing |
| Freshness | `loyalty freshness` | Warns after 26 hours since the last load, fails after 50 |
| Build | `loyalty build [--select ...] [--indirect-selection cautious]` | Models, snapshot, tests; failing rows go to the `audit` schema |
| Export for Power BI | `loyalty export` | Every mart and the test history to `exports/*.parquet` |
| Reference values for Power BI | `loyalty reference` | SQL side of the reconciliation, into `exports/reference_values.json` |
| Compare two warehouses | `loyalty compare A.duckdb B.duckdb` | Row-for-row, ignoring which run wrote a row; exits 1 on a difference |
| Tests | `docker compose run --rm --entrypoint pytest loyalty -q` or `uv run pytest` | About four minutes; builds dbt several times |
| Any dbt command | `docker compose run --rm --entrypoint dbt loyalty <command> --project-dir dbt --profiles-dir dbt` | |

Opt-in Airflow: [orchestration/README.md](../orchestration/README.md). Power BI: [bi/README.md](../bi/README.md).

## Diagnose

| Symptom | Where to look | What to do |
| --- | --- | --- |
| `load rejected: ... checksum` | The named file | It changed after it was written. Regenerate with `loyalty generate` |
| `load rejected: ... do not match the contract` | The file's header against `src/loyalty/config.py` | A source changed shape. Change the contract and staging together, deliberately |
| `earlier batches ... are not loaded` | `raw._load_log` | Load the missing batch first |
| Freshness fails | `raw._load_log.loaded_at` | No extract arrived in 50 hours. Load it, or investigate the source |
| A test fails | `audit.dq_test_result` for the run, then the table named in `failures_relation` | The failing rows are stored; fix the cause, then `loyalty build` |
| `This model has an enforced contract that failed` | The model and its YAML | A column's name or type changed; update the contract only if the change is intended |
| Incremental fact suspected wrong | `loyalty build --select fct_sales_line+` with `--full-refresh` on a copy, then `loyalty compare` | Any difference is a defect in [decision 0006](decisions/0006-incremental-boundary.md)'s rules |
| DuckDB file locked | Another process holding `warehouse/loyalty.duckdb` | Close it. DuckDB allows one writer |

## Recover

- **Start again:** `loyalty run-all` deletes the warehouse and rebuilds it. The snapshot is rebuilt correctly only this way, by replaying batch 1 then batch 2 ([decision 0002](decisions/0002-history-strategy.md)).
- **Rebuild the marts from what is already loaded:** `loyalty build`, adding `--full-refresh` through the dbt command to rebuild the incremental fact from scratch.
- **Clean up:** `docker compose down -v` removes the container's warehouse volume. Generated extracts, exports and dbt artefacts are ignored by Git and can be deleted.
