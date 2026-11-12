# Tests

Run inside the case's Linux container, with PostgreSQL up (see the case README):

```sh
docker compose run --rm --entrypoint pytest payrecon -q
```

To require PostgreSQL explicitly (the CI setting):

```sh
docker compose run --rm -e PAYRECON_REQUIRE_DB=1 --entrypoint pytest payrecon -q
```

Without that flag, an unavailable database skips the database fixtures so local generator-only work can continue. With `PAYRECON_REQUIRE_DB=1`, a connection failure fails the fixture and the test command exits non-zero. Each database test uses a disposable database; the configured role must be able to create and drop databases. Do not point tests at a production service.

The [Case 03 workflow](../../../.github/workflows/case-03-ci.yml) builds the locked Linux image, checks the CLI and formatting, runs all tests against its own PostgreSQL service with the flag enabled, and uploads JUnit results. It does not require Airflow or local `.env` files. Local reports in `evidence/ci/` are generated output excluded from Git and image builds. The workflow builds with the runner's UID so the non-root test process can write its mounted report directory.

- `test_database_requirement.py`: injects a refused connection and checks required-database failure versus optional local skipping.
- `test_stream_intake.py`: contract validation without Spark imports; disposable PostgreSQL tests for raw/quarantine/canonical publication, immutable identity conflicts, exact callback replay, transaction rollback and checkpoint lineage guards. The separate [real streaming check](../streaming/README.md) proves producer acknowledgements, actual Spark checkpoint restart and re-publication against Kafka; ordinary unit/database tests do not require Kafka or a JRE.
- `test_stream_live.py`: sequence gaps, applied-event eligibility, separate expiries, reference refresh of old minutes, exact callback/republication safety, immutable conflicts, projection rollback, writer fencing, generation isolation, identity diagnostics, frontier/progress guards and exact state parity with batch including over-refunds/reversals. The separate real live helper exercises Spark's stateful operator, checkpoint restart, duplicate batches and observed watermark drops.
- `test_stream_recovery.py`: bounded repair eligibility, unchanged plans/frontiers/identities, full-prefix and canonical checks, both-reader fencing, old timestamp preservation, repair-ID replay safety, audit-write rollback and diagnostic crash guards. The separate `streaming/check_recovery.py` actually terminates the consumer at both commit boundaries and verifies checkpoint continuation, late repair, independent batch parity and re-delivery.
- `test_stream_state_profile.py`: all 384 input event IDs are distinct, lifecycle/clock/schema fields validate, re-sends preserve semantic identity and the producer cannot target the primary topic. `streaming/check_state.py` proves actual repeated eviction in one continuous Spark process, with query/run identity, state metrics, duplicate/drop behaviour and retained business history. It is not a memory/throughput benchmark.
- `test_stream_progress.py`: idle/no-execution progress must not reserve the next batch ID; the later actual callback and zero-input watermark cleanup must both remain reportable. This regression is also exercised by A12's idle/re-send sequence.
- `test_stream_measurement.py`: nearest-rank percentiles, warm-up exclusion, unique event/coordinate coverage and rejection of missing, negative or non-finite latency samples. The separate `streaming/measure_stream.py` runs the finite current-clock profile; CI has no timing threshold.

- `test_generator.py`: the same seed gives byte-identical files; every planted defect P1–P16 is delivered; the random background follows the payment state machine and settles exactly what was captured, at the plan's fee. A clean background means any exception raised downstream can only come from a planted record.
- `test_loader.py` (marker `db`): each test creates and drops its own database. Malformed rows are quarantined while the rest of the file lands; a re-sent file lands nothing; a corrected file under the same name does land; a damaged arrival (checksum, header, missing file, row count) is rejected whole; the full delivery loaded twice lands once.

- `test_core.py` (marker `db`): the state machine table matches the documented one; each distinct event is counted once (P1); every payment's state equals an independent Python replay of the generator's events in sequence order (invariant 2); an out-of-order event is held until the gap fills (P2); an illegal transition is recorded and not applied (P3); an over-refund is rejected; events for an unknown merchant wait in quarantine until it arrives (P5); a corrected settlement line supersedes the original; a sequence-number clash stops the build; core is referentially whole without foreign keys; rebuilding a day before and after later loads gives identical tables (invariant 8).

- `test_recon.py` (marker `db`): every run explains every difference (invariant 3); the last run has exactly the planted exceptions with their signed amounts; P2, P5 and P7 lines await for their grace day, then match or become exceptions; P6 is pending, then missing; P9 waits, P10's fee error is raised at once; P13 is a duplicate while P12's re-sent file is not; P14's late file makes 473 payments missing for one day; amounts are never added across currencies; rerunning a day gives identical rows; each check reports a deliberately corrupted result; a failed run publishes nothing and is logged.
