# Payment monitoring and settlement reconciliation

**Status: milestone 1 in progress.** Built and tested: the seeded generator (planted defects P1–P16); landing into PostgreSQL with quarantine; core (distinct events, payment state by sequence number, illegal transitions, settlement lines). Not built yet: matching, exceptions, control totals and orchestration.

A fictional payment provider needs two views of the same payments: an operational one, within minutes, of what is failing; and a financial one, daily, proving that every captured payment was settled correctly and explaining every difference. The events arrive duplicated, out of order and late.

Planned stack:
- **Batch (milestone 1):** PostgreSQL with Python, orchestrated by the shared portfolio Airflow.
- **Streaming (milestone 2):** Kafka (KRaft) and Spark Structured Streaming.
- **Runtime:** Docker is required ([decision 0003](docs/decisions/0003-docker-required.md)).

## Run it

Docker is the only requirement. From this folder:

```sh
cp .env.example .env                        # then choose your own POSTGRES_PASSWORD
docker compose up -d --wait postgres
docker compose run --rm payrecon generate   # 31 arrival folders under data/generated (about 15 s)
docker compose run --rm payrecon migrate
docker compose run --rm payrecon load --arrival all
docker compose run --rm payrecon build-core       # as of the latest arrival, or --arrival YYYY-MM-DD
docker compose run --rm --entrypoint pytest payrecon -q
```

`load` is safe to repeat: a file already landed with the same name and checksum is skipped. `build-core` rebuilds core from everything that had arrived by a date, so rerunning a day gives the same result. `payrecon reset --yes` drops the case's schemas.

## Start here

- [Requirements](docs/requirements.md): decisions, settlement rules, acceptance criteria, invariants
- [Data model](docs/data-model.md): sources, planted defects, payment state machine, grain of every table
- [Metrics](docs/metrics.md)
- [Architecture](docs/architecture.md)
- [Decisions](docs/decisions/README.md)

[Return to portfolio](../../README.md).
