# Product hierarchy and master data quality

**Status: planned.** Optional specialist case. This case contains a planning brief and implementation placeholders, with no working pipeline, report, dataset or test suite yet.

Planned technologies: Fabric or Databricks, PySpark, SQL.

## Business problem

A fictional consumer-goods manufacturer needs to trace finished products through components and packaging, identify broken structures and calculate material quantities. Packaging analysts and master-data stewards need an explainable result at a stated effective date.

This case demonstrates engineering for packaging analysis. It is not a certified regulatory calculation and makes no claim of real compliance savings.

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
