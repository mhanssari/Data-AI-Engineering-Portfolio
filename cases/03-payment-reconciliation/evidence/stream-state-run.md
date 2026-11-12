# Repeated state eviction: bounded A12 validation

Recorded 12 November 2026 on Windows/Docker Desktop using Linux containers. **A12 passed for the fixed finite profile:** one continuous Spark run, repeated event-time advancement/eviction, duplicate/late controls and retained PostgreSQL history. These A12/reporting changes remain uncommitted; the new CI step/artifact has not run remotely. This is not a latency, throughput, general RAM-bound or long-duration soak result.

## Prior recovery delivery

Committed/pushed recovery as `e6c3c5e`. [Database CI](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43269557802) passed 92 tests in 152.09 seconds. [Case 03 Streaming](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43269557791) passed intake, live and actual crash/repair checks, uploaded both reports and cleaned its runner services. Downloaded `case-03-recovery-progress` and inspected both crash markers, one admitted repair, independent batch parity (six payments/eight applied minute groups) and consistency after re-delivery. Those results cover the recovery commit, not the new A12 code.

## Reproduce the profile

With the optional case image built and shared Kafka/case PostgreSQL healthy, from the repository root:

```sh
python cases/03-payment-reconciliation/streaming/check_state.py
```

The host helper uses standard-library Python and Docker. It creates one unique diagnostic topic/database/checkpoint root, starts one labelled continuous live container, and publishes eight cohorts seven event-time minutes apart. Each cohort has 24 new payment IDs and 48 new event IDs, with event timestamps spanning 48 seconds. All 384 source event IDs are distinct. The source is accelerated historical input on 15 October 2026, not paced real-time traffic. Trigger interval remains five seconds; the watermark delay remains ten minutes.

All cohort observations, the idle control and both re-sends used the same query ID `b5b184b6-4cd0-48f8-be44-9e87a1c277d9` and Spark run ID `63e45683-cefd-4480-af2d-6712c0f9f646`. Live generation: `fdf6273a-20e8-4456-adc4-d9a635134246`; topic: `case03.live-check-c3a9b13e0254`. This was not a fresh JVM per cohort.

## Observed state and retained history

After each acknowledged offset frontier, waited for the actual cleanup watermark before sampling:

| Cohort | Unique ledger events | Retained Spark keys | Cumulative keys removed | Reported operator bytes | Watermark (UTC, 15 October) |
| --- | ---: | ---: | ---: | ---: | --- |
| 0 | 48 | 48 | 0 | 14,064 | 09:50:47 |
| 1 | 96 | 96 | 0 | 26,736 | 09:57:47 |
| 2 | 144 | 144 | 0 | 40,048 | 10:04:47 |
| 3 | 192 | 144 | 48 | 51,568 | 10:11:47 |
| 4 | 240 | 144 | 96 | 52,080 | 10:18:47 |
| 5 | 288 | 144 | 144 | 51,568 | 10:25:47 |
| 6 | 336 | 144 | 192 | 52,080 | 10:32:47 |
| 7 | 384 | 144 | 240 | 52,080 | 10:39:47 |

Observed intermediate maximum was 192 retained keys; maximum reported operator memory was 52,080 bytes. Settled state stayed at 144 keys across five successive evictions while the ledger grew to 384 events. Guards were at most 192 settled keys and 240 intermediate keys for this fixed density/key size. The observed three-cohort retention must not be interpreted as exactly ten minutes of retained keys; watermark delay is not a direct key-age/RAM limit.

During a 12-second idle observation, the container remained running, query/run IDs stayed the same, and neither watermark nor retained-key count advanced. This observes multiple trigger opportunities, not a ten-minute wall-clock TTL experiment.

Re-sending the newest 48-event cohort increased the duplicate-drop metric by 48. Re-sending the oldest cohort after eviction increased the actual watermark-drop metric by 48. Neither changed any admitted-event, application, latest-payment or minute-table fingerprint. There was no inferred late-drop claim based only on source ingestion lag.

After stopping the continuous live process with SIGINT, drained intake separately so only one Spark workload ran at a time. Final retained test data: **480 physical deliveries, 384 canonical intake events, 384 admitted/applied live events, 192 payment states and 24 event-type/outcome minute groups**. The operational view had eight minute buckets, 144 authorisations, 48 declines, zero expiries and 25% decline rate in every bucket. Intake/live frontiers matched: partition 0 = 164, partition 1 = 178, partition 2 = 138 (exclusive offsets); recorded-frontier consistency was true.

Resumed the same live checkpoint with unchanged business fingerprints and query ID. The helper printed both final PASS messages and `State integration checks passed`, exited zero and removed only its own topic, database, checkpoint root and labelled container. The original demo and shared broker volume were preserved. Full report/logs are ignored output at `evidence/ci/state-integration.json` and `evidence/ci/state-live-c3a9b13e0254.log`; the new workflow is configured to upload `case-03-state-progress`.

## Incident found during the check

The first attempt completed all eight cohort/eviction observations but timed out at the first re-send after idle. Business rows were unchanged and Spark had executed the callback; the logger still showed the previous frontier. Investigation found that no-execution idle reports can reuse the next batch ID. The logger marked that future ID as seen before its actual callback, hiding subsequent progress.

Fixed reporting to consider only reports with an `addBatch` duration before marking their batch ID seen. Real empty watermark-cleanup callbacks still qualify. Added two focused regressions for idle → actual callback under the same ID and zero-input cleanup. The full continuous rerun above then passed, including the previously failing idle/re-send sequence. Failed attempt resources were cleaned and its log remains ignored output; it was not counted as a successful run.

## Validation boundary and next step

Three fixed-profile contract/identity tests passed in 0.40 seconds. After the reporting fix, **all 50 streaming tests passed in 29.87 seconds**, including required PostgreSQL fixtures. Financial transformations were unchanged, so the full financial suite was not repeated locally in this slice; the pushed recovery commit's 92-test CI is the separate baseline. The actual continuous integration check above verifies Spark behaviour rather than a mocked eviction algorithm.

The [decision](../docs/decisions/0011-observed-stream-state-bounds.md) owns the fixed guards. PostgreSQL history and progress records continue growing. Operator bytes are not JVM/container memory; this does not prove arbitrary-rate bounds, indefinite stability, checkpoint-disk bounds, Kafka retention expiry or database archival. The configured local container budgets and broker retention remain separate from these observations.

Final checks: Ruff lint passed and formatting reported 88 files already formatted; base/optional Compose configurations, `actionlint` and Git whitespace checks passed. All 135 relative Markdown links in changed/new documents and local handoff files resolved. Both final images built and passed import/helper-packaging smoke checks; the batch image still has no PySpark/Kafka producer/JRE and its CLI works with Kafka stopped. Retained demo is unchanged at 16 raw deliveries, three canonical events and eight quarantine rows, with canonical digest `1f27ca689a124e180370e8a6b0cea38b`. The primary topic remains empty. Kafka is stopped with all shared/demo state preserved; PostgreSQL remains running and Airflow remains stopped.

Commit `9a4e9db` was pushed to `case-03-payments`. Its [database CI](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43275451262) passed **97 tests in 119.500 seconds**, with zero failures/errors/skips. Its [streaming CI](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43275451283) passed intake, live, recovery, the new continuous-state check, evidence uploads and cleanup. Downloaded `case-03-state-progress` and verified eight cohorts with retained keys 48/96/144/144/144/144/144/144, cumulative removals 0/0/0/48/96/144/192/240, 480 deliveries/384 canonical events, consistent frontiers and unchanged same-query checkpoint resume. The new CI step and artifact are verified.

Next: the separate current-clock measurement, followed by a final clean-checkout review. Full-month historical replay remains separate validation; no new business decision blocks that sequence.
