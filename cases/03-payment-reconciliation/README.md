# Payment monitoring and settlement reconciliation

**Status: batch, shared Kafka, Spark intake/live and recovery have passing GitHub checks.** Built: the seeded generator (planted defects P1–P16); PostgreSQL landing/quarantine; sequence-derived core; reconciliation, grace, exceptions and controls; shared Airflow orchestration; ten-minute watermarked Spark admission and SQL operational views; controlled backfill and abrupt consumer recovery. A repeated-eviction profile also passes locally (A12): [state evidence](evidence/stream-state-run.md) records actual bounds, a reporting fix and limits. Latency/throughput measurement remains outstanding. See [clean batch](evidence/batch-clean-setup-run.md), [intake](evidence/stream-intake-run.md), [live](evidence/stream-live-run.md) and [recovery](evidence/stream-recovery-run.md) evidence.

A fictional payment provider needs two views of the same payments: an operational one, within minutes, of what is failing; and a financial one, daily, proving that every captured payment was settled correctly and explaining every difference. The events arrive duplicated, out of order and late.

Stack:
- **Batch (milestone 1):** PostgreSQL with Python, orchestrated by the shared portfolio Airflow.
- **Streaming:** shared Kafka (KRaft), optional Spark intake/live readers, acknowledged fixtures, SQL operational views and bounded controlled backfill.
- **Runtime:** Docker is required ([decision 0003](docs/decisions/0003-docker-required.md)).

## Run it

Docker is the only requirement. From this folder:

```sh
cp .env.example .env                        # then choose your own POSTGRES_PASSWORD
docker compose build payrecon               # install the locked environment for this checkout
docker compose up -d --wait postgres
docker compose run --rm payrecon generate   # 31 arrival folders under data/generated (about 15 s)
docker compose run --rm payrecon migrate
docker compose run --rm payrecon load --arrival all
docker compose run --rm payrecon build-core       # as of the latest arrival, or --arrival YYYY-MM-DD
docker compose run --rm payrecon reconcile        # builds core and reconciles, same --arrival option
docker compose run --rm payrecon run-all          # or replay every day in order: load, then reconcile (about 3 min)
docker compose run --rm --entrypoint pytest payrecon -q
```

The [CI workflow](../../.github/workflows/case-03-ci.yml) runs the locked Linux image against a disposable PostgreSQL service, with database tests required rather than skipped. See [test guidance](tests/README.md) for the same requirement locally. DAG checks run separately in the shared platform workflow.

`load` is safe to repeat: a file already landed with the same name and checksum is skipped. `build-core` rebuilds core from everything that had arrived by a date, so rerunning a day gives the same result. `reconcile` fails, and publishes nothing, if any invariant check fails. A payment or unmatched line waits one grace day before it becomes an exception ([decision 0005](docs/decisions/0005-reconciliation-runs-and-grace.md)). `payrecon reset --yes` drops the case's schemas in the selected database, including stream state if present, then applies batch migrations. For the optional streaming image, separate database and checkpoint precautions, follow the [intake run guide](streaming/README.md).

## Start here

- [Clean batch setup and review](evidence/batch-clean-setup-run.md): isolated committed checkout, 31 arrivals, exact fixture counts and remaining limits
- [Streaming run guide and checks](streaming/README.md): optional runtime, quarantine, intake/live checkpoints, minute views and phased replay
- [Streaming design](docs/streaming-design.md): implemented intake/live boundaries, confirmed metric/lateness choices and remaining controlled recovery
- [Airflow recovery evidence](evidence/airflow-late-file-run.md): late-file recovery and unchanged results on rerun
- [CI validation evidence](evidence/ci-local-run.md): 47 passing tests locally and on GitHub; unavailable-database failure and JUnit artifact verified
- [Requirements](docs/requirements.md): decisions, settlement rules, acceptance criteria, invariants
- [Data model](docs/data-model.md): sources, planted defects, payment state machine, grain of every table
- [Metrics](docs/metrics.md)
- [Architecture](docs/architecture.md)
- [Decisions](docs/decisions/README.md)

## Limits and trade-offs

Core is rebuilt from raw for each arrival date, which makes late corrections and historical reruns straightforward at this fixture size. Larger workloads would need incremental processing with equivalent recovery checks. Only one writer may target a case database at a time: Airflow serialises its runs, but concurrent CLI writers are not blocked by a database lock.

Merchant pricing uses the latest reference data available to each as-of build; historical price-plan versions are not modelled. This is a local Docker demonstration, with no high availability or cloud deployment. State eviction is validated for a finite fixed-density profile, not arbitrary input rates, total RAM or retention expiry. Measured latency/throughput remains future work. The live sink rewalks affected histories through one driver/database; repair requires complete retained input for a bounded prefix. This is not a distributed sink benchmark or general broker-loss recovery system. See [state limits](evidence/stream-state-run.md), [recovery limits](evidence/stream-recovery-run.md) and [metrics](docs/metrics.md).

[Return to portfolio](../../README.md).
