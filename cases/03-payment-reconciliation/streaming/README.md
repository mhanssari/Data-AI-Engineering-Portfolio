# Case 03 streaming intake

Implemented: an optional producer/Spark runtime and **unwatermarked durable intake**. The live query, ten-minute watermark, SQL operational projections and controlled backfill remain planned in the [streaming design](../docs/streaming-design.md). See [decision 0008](../docs/decisions/0008-streaming-runtime-and-intake.md) and [validation evidence](../evidence/stream-intake-run.md).

## Run the small fixture

First create the case `.env` using the [case guide](../README.md). Keep shared Airflow stopped for laptop memory headroom. The commands below are from this case directory, using Docker Compose v2:

```sh
docker compose -f ../../platform/kafka/compose.yaml up -d --wait
docker compose up -d --wait postgres
docker compose -f compose.yaml -f streaming/compose.yaml build stream
```

Create a case-owned diagnostic topic, then prepare the **separate** streaming database, produce eight acknowledged deliveries, and drain them:

```sh
docker compose -f ../../platform/kafka/compose.yaml run --rm -T -e PAYRECON_KAFKA_TOPIC=case03.intake-check -v "${PWD}/streaming:/case:ro" tools /case/init-kafka.sh
docker compose -f compose.yaml -f streaming/compose.yaml run --rm stream stream-prepare
docker compose -f compose.yaml -f streaming/compose.yaml run --rm stream stream-produce-fixture
docker compose -f compose.yaml -f streaming/compose.yaml run --rm stream stream-intake --available-now
docker compose -f compose.yaml -f streaming/compose.yaml run --rm --entrypoint python stream /app/streaming/probe_intake.py
```

`${PWD}` works in PowerShell and POSIX shells; use a full path if your shell does not expand it. Initial build downloads the JRE, Python dependencies and matching Kafka connector; startup uses the connector JARs already in the image. Host Java, Spark and Kafka packages are unnecessary. The batch image and Airflow environment do not install the optional streaming dependency group.

Expected first snapshot: eight deliveries, three canonical events and four quarantined deliveries. The capture is duplicated, and its authorisation arrives a day late. There is no state-machine projection in this slice: all three valid events are retained for later SQL application. Malformed JSON, a string amount, a tombstone and an unknown schema version retain their original bytes and coordinates.

Repeat `stream-intake --available-now` with the same checkpoint: durable rows and first-commit metadata stay unchanged. Republish the fixture and drain again: deliveries become 16, quarantine becomes eight, and canonical events remain three. Omit `--available-now` for five-second continuous triggers; stopping and restarting uses the same checkpoint. This fixture producer is deliberately restricted to `case03.intake-check*`, leaving `case03.payment-events.v1` unused.

For an automated producer/Spark/restart/republication check, with both services healthy and the image built, run this from the repository root:

```sh
python cases/03-payment-reconciliation/streaming/check_intake.py
```

This optional host helper uses only Python's standard library and Docker. It creates a unique test topic, database and checkpoint, then removes only those resources. It never stops the shared broker or deletes shared volumes. The [streaming workflow](../../../.github/workflows/case-03-streaming.yml) runs the same check on its disposable runner; the financial/unit tests remain in [database CI](../../../.github/workflows/case-03-ci.yml).

## State and recovery boundaries

- Base Compose starts without Kafka. The optional overlay joins `portfolio-kafka`, caps the Spark container at 3 GiB, and mounts case-owned `stream-state` at `/state`.
- Default database is `payments_stream`, selected by `PAYRECON_STREAM_DB`. `stream-prepare` creates/migrates only `payments_stream*`; it refuses the batch database and never drops a database. One dataset and its source-clock bounds bind to this database.
- Defaults are dataset `case03-intake-v1`, source clock `[2026-10-01T00:00:00Z, 2026-11-01T00:00:00Z)`, topic `case03.intake-check`, checkpoint `/state/intake-v1`. The CLI exposes explicit overrides. Use a distinct database/checkpoint when changing datasets.
- Checkpoint sidecar, database lineage and actual Kafka cluster/topic IDs must agree. Topic recreation, an unregistered non-empty checkpoint, a removed sidecar or missing Spark metadata after a database commit fail visibly. A new checkpoint path creates a new generation; existing transport/event uniqueness still prevents duplicated canonical rows.
- Every batch archives raw deliveries, canonical events, quarantine and its commit record in one PostgreSQL transaction. A repeated committed callback checks its physical-input hash and returns the stored result. A session advisory lock excludes another intake writer for the same dataset.
- An event-ID or payment-sequence conflict is archived as blocking evidence without overwriting the canonical event. The query then fails, and replaying the same callback still fails. This slice has no automatic conflict-resolution command: inspect the raw evidence and make a deliberate source/recovery decision.
- Spark uses earliest available offsets for a new lineage and `failOnDataLoss=true`. Kafka retention is finite; intake is retained without a watermark but has no backup/archive policy yet. This is replay-safe local database publication, with no end-to-end exactly-once or broker-loss guarantee.
- Checkpoint files and PostgreSQL are separate durability domains. Sidecar/database registration is not a distributed transaction; an ambiguous failure requires explicit recovery rather than silent lineage reuse. The runtime checks Spark metadata existence, not arbitrary checkpoint corruption.
- `payrecon reset --yes` affects the selected database and includes the stream schema; it then applies **batch** migrations. Do not use it for routine stream preparation. A surviving stream sidecar after a database reset is deliberately refused. Recovery must explicitly reconcile database, checkpoint and retained source history.

The [case topic initializer](init-kafka.sh) owns topic settings, refuses namespaces outside `case03.`, and rejects drift without changing an existing topic. Kafka lifecycle/storage remains owned by the [shared platform](../../../platform/kafka/README.md). To stop after local work, use `docker compose -f ../../platform/kafka/compose.yaml stop`; preserve its volume, case checkpoint volume and PostgreSQL state.
