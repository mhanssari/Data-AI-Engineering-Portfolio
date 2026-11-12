# Payment monitoring and settlement reconciliation

**Status: designed, not yet built.** Requirements, settlement rules, planted defects and the proposed grain are written; no generator, pipeline or tests exist yet.

A fictional payment provider needs two views of the same payments: an operational one, within minutes, of what is failing; and a financial one, daily, proving that every captured payment was settled correctly and explaining every difference. The events arrive duplicated, out of order and late.

Planned stack:
- **Batch (milestone 1):** PostgreSQL with Python, orchestrated by the shared portfolio Airflow.
- **Streaming (milestone 2):** Kafka (KRaft) and Spark Structured Streaming.
- **Runtime:** Docker is required ([decision 0003](docs/decisions/0003-docker-required.md)).

## Start here

- [Requirements](docs/requirements.md): decisions, settlement rules, acceptance criteria, invariants
- [Data model](docs/data-model.md): sources, planted defects, payment state machine, grain of every table
- [Metrics](docs/metrics.md)
- [Architecture](docs/architecture.md)
- [Decisions](docs/decisions/README.md)

[Return to portfolio](../../README.md).
