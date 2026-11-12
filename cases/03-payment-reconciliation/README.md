# Payment monitoring and settlement reconciliation

**Status: milestone 1 (batch) validated locally, with database CI passing on GitHub.** Built: the seeded generator (planted defects P1–P16); landing into PostgreSQL with quarantine; core (distinct events, payment state by sequence number, illegal transitions, settlement lines); reconciliation (matching, a one-day grace period, exceptions, control totals and invariant checks per run); an opt-in DAG on the shared Airflow. Shared Kafka and the case topic are now validated locally, but the payment producer and Spark/intake/live application are not built. See [clean batch evidence](evidence/batch-clean-setup-run.md), [orchestration](orchestration/README.md) and [Kafka foundation](../../platform/kafka/evidence/foundation-run.md).

A fictional payment provider needs two views of the same payments: an operational one, within minutes, of what is failing; and a financial one, daily, proving that every captured payment was settled correctly and explaining every difference. The events arrive duplicated, out of order and late.

Stack:
- **Batch (milestone 1):** PostgreSQL with Python, orchestrated by the shared portfolio Airflow.
- **Streaming (milestone 2):** shared Kafka (KRaft) foundation built; Spark application planned.
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

`load` is safe to repeat: a file already landed with the same name and checksum is skipped. `build-core` rebuilds core from everything that had arrived by a date, so rerunning a day gives the same result. `reconcile` fails, and publishes nothing, if any invariant check fails. A payment or unmatched line waits one grace day before it becomes an exception ([decision 0005](docs/decisions/0005-reconciliation-runs-and-grace.md)). `payrecon reset --yes` drops the case's schemas.

## Start here

- [Clean batch setup and review](evidence/batch-clean-setup-run.md): isolated committed checkout, 31 arrivals, exact fixture counts and remaining limits
- [Streaming design (planned)](docs/streaming-design.md): shared Kafka, source contract, confirmed metric/lateness choices and recovery plan
- [Airflow recovery evidence](evidence/airflow-late-file-run.md): late-file recovery and unchanged results on rerun
- [CI validation evidence](evidence/ci-local-run.md): 47 passing tests locally and on GitHub; unavailable-database failure and JUnit artifact verified
- [Requirements](docs/requirements.md): decisions, settlement rules, acceptance criteria, invariants
- [Data model](docs/data-model.md): sources, planted defects, payment state machine, grain of every table
- [Metrics](docs/metrics.md)
- [Architecture](docs/architecture.md)
- [Decisions](docs/decisions/README.md)

## Limits and trade-offs

Core is rebuilt from raw for each arrival date, which makes late corrections and historical reruns straightforward at this fixture size. Larger workloads would need incremental processing with equivalent recovery checks. Only one writer may target a case database at a time: Airflow serialises its runs, but concurrent CLI writers are not blocked by a database lock.

Merchant pricing uses the latest reference data available to each as-of build; historical price-plan versions are not modelled. This is a local Docker demonstration, with no high availability or cloud deployment. Streaming, measured event latency and throughput remain future milestones. See the [clean batch evidence](evidence/batch-clean-setup-run.md) for reproduction results and the [metric catalogue](docs/metrics.md) for implemented versus proposed outputs.

[Return to portfolio](../../README.md).
