# Architecture decisions

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-shared-airflow.md) | One shared Airflow for the portfolio | Accepted |
| [0002](0002-money-in-minor-units.md) | Money in integer minor units, one currency at a time | Accepted |
| [0003](0003-docker-required.md) | Docker is required for this case | Accepted |
| [0004](0004-core-rebuilt-as-of-arrival.md) | Core rebuilt whole from raw, as of an arrival date | Accepted |

To record when built: event ordering and deduplication in the stream; the watermark and retention; the sink's idempotency; the backfill route for very late corrections.

Copy [the template](TEMPLATE.md) to a numbered file when a material choice is made. Status values: proposed, accepted or superseded.
