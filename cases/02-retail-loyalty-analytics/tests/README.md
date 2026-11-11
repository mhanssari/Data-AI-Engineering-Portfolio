# Tests

Python tests for everything outside dbt. dbt's own tests (schema, unit and business invariants) live in the dbt project.

| File | What it proves |
| --- | --- |
| [test_generator.py](test_generator.py) | Same seed, same bytes; each planted defect is where it should be; the background population is clean (returns match sales and never exceed them, sales happen at open stores after enrolment, reward status agrees with the event log) |
| [test_loader.py](test_loader.py) | A batch lands every row in its manifest; reloading lands nothing; raw is append-only; batches arrive in order; a changed file, a changed column or a failure part-way through leaves nothing behind |
| [test_dimensions.py](test_dimensions.py) | After each batch: an inferred product is completed, a store version takes effect on its date, a corrected enrolment moves the cohort, the as-of date marks partial months |
| [test_loyalty_marts.py](test_loyalty_marts.py) | Three events on one purchase count its revenue once (D3); reward status resolved from the event log (D13, D14); only planted entitlements disagree with their status column; open rewards stay out of the redemption denominator |
| [test_sales_facts.py](test_sales_facts.py) | Planted outcomes D3–D6, D9, D11; incremental equals full refresh after batch 2, after a store version arrives late and after a sale arrives after its return; the incremental run rewrites only what changed; a rerun with nothing new rewrites nothing |
| [test_staging_and_snapshot.py](test_staging_and_snapshot.py) | Batches replayed through dbt in order: duplicates kept once, late corrections win, late reference data arrives, the snapshot keeps the pre-correction member, a stale extract fails freshness |

Run with `uv run pytest`.
