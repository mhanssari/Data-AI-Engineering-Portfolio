# Retail loyalty and customer analytics

**Status: planned.** Second flagship. This case contains a planning brief and implementation placeholders, with no working pipeline, report, dataset or test suite yet.

Planned technologies: Snowflake, dbt, SQL, Python, Power BI.

## Business problem

A fictional retailer has inconsistent definitions of active customers, loyalty participation and redemption. Marketing needs reliable cohorts and inactivity signals; store managers need comparable performance; finance needs sales totals that reconcile after returns.

## Start here

- [Requirements](docs/requirements.md)
- [Architecture](docs/architecture.md)
- [Data model](docs/data-model.md)
- [Metric definitions](docs/metrics.md)
- [Decisions](docs/decisions/README.md)
- [Runbook](docs/runbook.md)
- [Evidence](evidence/README.md)

## Setup and execution

Not implemented. Choose the runtime and compatible dependencies at the first implementation milestone. The comment-only pyproject.toml is reserved for that configuration. .env.example contains no credentials and no runtime variables are required yet.

## Validation and results

No tests or benchmarks have been run for this case. Acceptance scenarios are proposals in docs/requirements.md. Record actual commands and results as the implementation is built.

[Return to portfolio](../../README.md).
