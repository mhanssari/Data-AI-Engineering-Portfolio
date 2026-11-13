# Python implementation

The case package is [payrecon](payrecon/). Start with [the CLI](payrecon/cli.py), then follow:

- Batch: [seeded generator](payrecon/generator.py), [validated loader](payrecon/loader.py), [core rebuild](payrecon/core.py) and [reconciliation](payrecon/recon.py).
- Streaming: [contract](payrecon/streaming/contract.py), [Spark runtime](payrecon/streaming/runtime.py), [intake](payrecon/streaming/intake.py), [live sink](payrecon/streaming/live.py), [lineage](payrecon/streaming/lineage.py) and [controlled recovery](payrecon/streaming/recovery.py).
- Diagnostics: [reported-frontier status](payrecon/streaming/progress.py), fixed [state profile](payrecon/streaming/state_profile.py) and [measurement arithmetic](payrecon/streaming/measurement.py).

SQL owns financial/state transformations. Spark and Kafka imports stay lazy so the batch environment remains independent of the optional streaming dependencies. See the [case guide](../README.md) and [test guidance](../tests/README.md) for verified commands.
