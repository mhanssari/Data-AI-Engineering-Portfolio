# Case 03 streaming intake

Implemented: an optional producer/Spark runtime, **unwatermarked durable intake**, an independent ten-minute watermarked live query, transactional SQL projections and bounded controlled backfill. The [streaming design](../docs/streaming-design.md) separates these guarantees from remaining sustained-state and measurement work. See [intake evidence](../evidence/stream-intake-run.md), [live evidence](../evidence/stream-live-run.md), [recovery evidence](../evidence/stream-recovery-run.md) and [decision 0010](../docs/decisions/0010-controlled-stream-recovery.md).

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

Expected first intake snapshot: eight deliveries, three canonical events and four quarantined deliveries. The capture is duplicated, and its authorisation arrives a day late. Intake retains all three valid events independently of the live query. Malformed JSON, a string amount, a tombstone and an unknown schema version retain their original bytes and coordinates.

Repeat `stream-intake --available-now` with the same checkpoint: durable rows and first-commit metadata stay unchanged. Republish the fixture and drain again: deliveries become 16, quarantine becomes eight, and canonical events remain three. Omit `--available-now` for five-second continuous triggers; stopping and restarting uses the same checkpoint. This fixture producer is deliberately restricted to `case03.intake-check*`, leaving `case03.payment-events.v1` unused.

For an automated producer/Spark/restart/republication check, with both services healthy and the image built, run this from the repository root:

```sh
python cases/03-payment-reconciliation/streaming/check_intake.py
```

This optional host helper uses only Python's standard library and Docker. It creates a unique test topic, database and checkpoint, then removes only those resources. It never stops the shared broker or deletes shared volumes. The [streaming workflow](../../../.github/workflows/case-03-streaming.yml) runs the same check on its disposable runner; the financial/unit tests remain in [database CI](../../../.github/workflows/case-03-ci.yml).

## Add the independent live view

After intake setup, from the case directory:

```sh
# Also applies the additive live migration when upgrading the intake-only database.
docker compose -f compose.yaml -f streaming/compose.yaml run --rm stream stream-prepare
docker compose -f compose.yaml -f streaming/compose.yaml run --rm stream stream-reference
docker compose -f compose.yaml -f streaming/compose.yaml run --rm stream stream-live --available-now
# Copy the live generation UUID printed by stream-live; do not use the intake generation.
docker compose -f compose.yaml -f streaming/compose.yaml run --rm stream stream-status --generation YOUR_LIVE_GENERATION_UUID
```

Both readers default to the existing diagnostic topic/dataset and use distinct `/state/intake-v1` and `/state/live-v1` checkpoints. The live reader uses strict contract/clock validation before watermark-bounded event-ID deduplication. SQL applies complete payment histories in source sequence order, holds gaps, records illegal transitions and replaces affected minute buckets. It never writes the financial batch tables.

`stream-reference` builds the seed-3303 merchant snapshot independently of financial runs. Default arrival frontier is 2 October; `--arrival 2026-10-12` also admits the late merchant according to the same source delivery calendar as batch. Stop the live reader before reference refresh: a database lock excludes concurrent live writes and refreshes. Intake can continue. Refresh atomically rebuilds affected histories and old minutes across live generations; backwards frontiers and seed changes are refused.

`stream.operation_minute` is an SQL view at generation × UTC minute × merchant × currency grain. Select one generation. It exposes the [defined decline percentage](../docs/metrics.md), expiry count and separate held/illegal/unknown-reference/identity-mismatch diagnostics. `stream.payment_latest` contains current sequence-derived state; `stream.event_application` explains each admitted event's outcome. Use a PostgreSQL client with your ignored local credentials to inspect these tables.

Status is deliberately limited to recorded source frontiers: it checks intake/live offset equality, reporting through the latest sink commits, immutable conflicts, reference holds and missing/unverified admitted events. The broker can have newer data. Unknown reference data, progress lag or an excluded late event keeps the consistency flag false; a successful Spark drain alone does not establish source completeness. Progress reports are separate from sink transactions and can lag or be lost on a crash.

Run the phased, isolated real live check from the repository root:

```sh
python cases/03-payment-reconciliation/streaming/check_live.py
```

It uses the same built image and healthy services, with a unique topic/database/checkpoint root. Phases demonstrate a held sequence gap, decision/expiry/illegal-event eligibility, stateful checkpoint restart, cross-batch duplicates, reference refresh and actual watermark exclusion. It leaves the omitted event in intake and reports repair required; the separate recovery check below demonstrates repair. The fixture producer supports `stream-produce-live-fixture --topic case03.live-check --phase initial|complete|repeat|advance|late|late-gap`; use the helpers for isolated setup and phase ordering. Historical source timestamps do not measure real-time latency.

## Controlled recovery

Run `stream-prepare` once to apply the additive recovery audit migration. Stop both readers after they have drained to the same target offsets; reference refresh must also be idle. Capture a plan for the affected **live generation**, from the case directory:

```sh
docker compose -f compose.yaml -f streaming/compose.yaml run --rm -T stream stream-backfill-plan --generation YOUR_LIVE_GENERATION_UUID
```

Save the returned JSON as UTF-8 in an ignored local file, for example `evidence/ci/repair-plan.json`. Inspect its dataset/clock, cluster/topic IDs, reader generations, exclusive partition offsets and reference seed/arrival/hash. Then apply that exact plan with a unique repair ID and a concrete reason:

```sh
docker compose -f compose.yaml -f streaming/compose.yaml run --rm stream stream-backfill --plan /app/evidence/ci/repair-plan.json --repair-id late-authorisation-001 --reason "Recover the authorisation omitted behind the observed watermark"
docker compose -f compose.yaml -f streaming/compose.yaml run --rm stream stream-status --generation YOUR_LIVE_GENERATION_UUID
docker compose -f compose.yaml -f streaming/compose.yaml run --rm stream stream-live --available-now
```

Both writers are fenced during repair. A changed plan/frontier/reference, immutable conflict, unverified live event or missing source prefix fails without partial publication. The first MVP repairs the full current generation from one topic epoch, requiring complete transport history from offset zero: at most 20,000 deliveries and 64 MiB of key/value bytes. It does not support arbitrary historical/partial scopes, mixed epochs or missing retained source history. The broker may have newer data outside the declared prefix.

Repair admits only validated missing canonical events, preserves existing content/timestamps, rebuilds payment histories/minutes, and commits the audit in `ops.stream_backfill` atomically. Poison records remain quarantined. Repeating the same repair ID and request reuses its recorded result; changing the request under that ID fails. Spark checkpoint files and query batch/progress records are untouched. Resume the same checkpoint; PostgreSQL uniqueness still protects the repaired events against later delivery. Failed attempts roll back completely and are visible in caller logs, rather than committed failed-audit rows.

For the actual abrupt-exit and repair demonstration, from the repository root with both services healthy and the optional image built:

```sh
python cases/03-payment-reconciliation/streaming/check_recovery.py
```

It uses unique disposable resources, exits the live consumer inside the database transaction and just after commit, checks database rows against real Spark commit/offset files, and resumes the same checkpoint. A later phase excludes an authorisation behind the observed watermark; repair rewalks the held capture and is compared with an actual batch run in another disposable database. Repeated repair and later re-delivery must leave business rows unchanged. The helper writes ignored `evidence/ci/recovery-integration.json` and cleans only its own topic, databases, checkpoint root and plan file.

The `stream-live --crash-at before-commit|after-commit` option is diagnostic only. It requires the live role, a `case03.live-check-*` topic and a disposable `payments_stream_check_*` database. It exits on the first non-empty callback with code 86 or 87; remove the option when restarting. Ordinary live processing has no crash hook enabled.

## Repeated state eviction (A12)

With the optional image built and shared Kafka/PostgreSQL healthy, from the repository root:

```sh
python cases/03-payment-reconciliation/streaming/check_state.py
```

This profile runs one continuous live query and publishes eight cohorts, seven **event-time** minutes apart. Each cohort has 24 new payments and 48 unique events; the ledger grows to 384 events while old Spark keys must be evicted repeatedly. It waits for acknowledged partition offsets and the actual cleanup watermark, records state rows/operator bytes, then checks an idle period, a recent duplicate cohort and a re-send of already evicted events. Business fingerprints must remain unchanged by the re-sends.

The helper labels and stops only its own live container with SIGINT, drains intake after the live process stops, and resumes the same checkpoint. It expects 480 physical deliveries and 384 canonical events retained in intake, 192 current payment states and eight operational minute buckets at the declared 25% decline rate. Resources are isolated under a unique topic/database/checkpoint root; the existing demo and shared broker volume are preserved. Reports/logs are ignored output under `evidence/ci/`, uploaded by the new CI step when it runs.

The [profile decision](../docs/decisions/0011-observed-stream-state-bounds.md) and [evidence](../evidence/stream-state-run.md) separate finite observed state bounds from a general memory guarantee. PostgreSQL history continues growing; state-operator bytes are not JVM/container memory. This is accelerated historical validation, with no latency/throughput, retention-expiry, checkpoint-disk or long-duration soak claim.

## State and recovery boundaries

- Base Compose starts without Kafka. The optional overlay joins `portfolio-kafka`, caps the Spark container at 3 GiB, and mounts case-owned `stream-state` at `/state`.
- Default database is `payments_stream`, selected by `PAYRECON_STREAM_DB`. `stream-prepare` creates/migrates only `payments_stream*`; it refuses the batch database and never drops a database. One dataset and its source-clock bounds bind to this database.
- `stream_002_live` adds role-aware lineage and live tables; old intake sidecars without an explicit role remain intake markers. Run preparation before using this checkout against an intake-only database. Do not interchange intake/live checkpoint paths.
- Defaults are dataset `case03-intake-v1`, source clock `[2026-10-01T00:00:00Z, 2026-11-01T00:00:00Z)`, topic `case03.intake-check`, checkpoint `/state/intake-v1`. The CLI exposes explicit overrides. Use a distinct database/checkpoint when changing datasets.
- Checkpoint sidecar, database lineage and actual Kafka cluster/topic IDs must agree. Topic recreation, an unregistered non-empty checkpoint, a removed sidecar or missing Spark metadata after a database commit fail visibly. A new checkpoint path creates a new generation; existing transport/event uniqueness still prevents duplicated canonical rows.
- Every batch archives raw deliveries, canonical events, quarantine and its commit record in one PostgreSQL transaction. A repeated committed callback checks its physical-input hash and returns the stored result. A session advisory lock excludes another intake writer for the same dataset.
- An event-ID or payment-sequence conflict is archived as blocking evidence without overwriting the canonical event. The affected query fails, and replaying the same callback still fails. A conflict hidden by Spark deduplication can still be found by intake and makes status untrusted. There is no automatic conflict-resolution command: inspect the raw evidence and make a deliberate source/recovery decision.
- Spark uses earliest available offsets for a new lineage and `failOnDataLoss=true`. Kafka retention is finite; intake is retained without a watermark but has no backup/archive policy yet. This is replay-safe local database publication, with no end-to-end exactly-once or broker-loss guarantee.
- Checkpoint files and PostgreSQL are separate durability domains. Sidecar/database registration is not a distributed transaction; an ambiguous failure requires explicit recovery rather than silent lineage reuse. The runtime checks Spark metadata existence, not arbitrary checkpoint corruption.
- `payrecon reset --yes` affects the selected database and includes the stream schema; it then applies **batch** migrations. Do not use it for routine stream preparation. A surviving stream sidecar after a database reset is deliberately refused. Recovery must explicitly reconcile database, checkpoint and retained source history.

The [case topic initializer](init-kafka.sh) owns topic settings, refuses namespaces outside `case03.`, and rejects drift without changing an existing topic. Kafka lifecycle/storage remains owned by the [shared platform](../../../platform/kafka/README.md). To stop after local work, use `docker compose -f ../../platform/kafka/compose.yaml stop`; preserve its volume, case checkpoint volume and PostgreSQL state.
