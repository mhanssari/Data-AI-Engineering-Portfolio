# Tests

Python tests for everything outside dbt. dbt's own tests (schema, unit and business invariants) live in the dbt project.

| File | What it proves |
| --- | --- |
| [test_generator.py](test_generator.py) | Same seed, same bytes; each planted defect is where it should be; the background population is clean (returns match sales and never exceed them, sales happen at open stores after enrolment, reward status agrees with the event log) |
| [test_loader.py](test_loader.py) | A batch lands every row in its manifest; reloading lands nothing; raw is append-only; batches arrive in order; a changed file, a changed column or a failure part-way through leaves nothing behind |

Run with `uv run pytest`.
