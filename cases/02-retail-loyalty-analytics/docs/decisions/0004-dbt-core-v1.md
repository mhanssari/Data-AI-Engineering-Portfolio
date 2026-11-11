# 0004 dbt Core v1, kept ready for v2

Status: accepted

Date: 11 November 2026

## Context

When this case started, dbt Labs had made dbt v2, a rewrite of the engine in Rust, the default for new installations (PyPI name `dbt`). dbt Core v1 (`dbt-core` with adapter packages) "remains fully supported", but new capabilities will land in v2 only. v2 lists DuckDB as supported from the command line only, downloads adapter drivers from a dbt Labs CDN on first run, and is distributed under its own licence agreement; the Apache 2.0 runtime is a separate `dbt-oss` package.

## Options

1. **dbt v2.** The direction of travel, faster parsing, stricter validation. A newer engine on DuckDB, a network fetch on first run, and a licence not yet reviewed for this repository.
2. **dbt Core v1 (`dbt-core` 1.12.6, `dbt-duckdb` 1.11.1).** Apache 2.0, what most teams run today, pure Python packages pinned in `uv.lock`, no network at run time.

## Decision and rationale

Option 2, written so that moving to v2 is a version change, not a rewrite: no deprecation warnings, `--select` never `--models`, `data_tests:` not `tests:`, no unknown keys in YAML, no `meta` read through `config.get()`.

## Consequences and validation

- CI fails on deprecation warnings (`--warn-error-options`), so v2-readiness is checked on every change instead of being a claim.
- A trial run under v2 is a sensible later step, recorded in `evidence/` if done. It is not needed for any result here.
