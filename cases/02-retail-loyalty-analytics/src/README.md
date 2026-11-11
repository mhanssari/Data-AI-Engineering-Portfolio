# Source code

The `loyalty` package: everything outside dbt.

| Module | Purpose |
| --- | --- |
| [config.py](loyalty/config.py) | Paths, the observation period, batch windows, and the column contract for each extract |
| [scenarios.py](loyalty/scenarios.py) | Planted defects D1–D15: records and expected outcomes |
| [generator.py](loyalty/generator.py) | Seeded simulation of the period, cut into two batch extracts |
| [loader.py](loyalty/loader.py) | Validated, ordered, all-or-nothing, append-only landing into `raw` |
| [transform.py](loyalty/transform.py) | Runs dbt in its own process against a given warehouse file |
| [cli.py](loyalty/cli.py) | `loyalty generate`, `load`, `build`, `run-all` |
