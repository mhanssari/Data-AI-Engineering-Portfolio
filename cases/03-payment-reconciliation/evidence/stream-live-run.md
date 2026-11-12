# Watermarked live view and SQL projections: local validation

Recorded 12 November 2026 on Windows/Docker Desktop with Linux containers. **The independent live query and SQL operational projections are implemented and locally validated.** These live changes remain uncommitted; the extended live workflow has not run remotely. A9 passed on the small deterministic fixture. Forced consumer crash recovery, controlled backfill parity and sustained eviction testing (A10–A12) remain open.

## Previous slice delivered

Intake was committed/pushed as `1848bd9` on `case-03-payments`. All four resulting workflows passed: [Case 03 Streaming](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43257252331), [database CI](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43257252189), [Platform Kafka](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43257252008) and [Platform Airflow](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43257252024). Inspected successful logs: the real intake check completed first drain, restart and re-publication; database CI passed 68 tests in 141.20 s. These results cover the pushed intake commit, not this new live code.

## Implemented boundary

The [live decision](../docs/decisions/0009-live-state-and-operational-projections.md) records strict shared-decoder validation before the ten-minute watermark, one bounded Spark deduplication operator, separate intake/live lineage/checkpoints and transactional SQL publication. Batch/live share transition values and the sequence walk through explicit relative SQL includes. SQL remains authoritative for state, exact minor-unit money and applied-event eligibility. The [metric catalogue](../docs/metrics.md) owns definitions.

The independently seeded reference snapshot has a recorded arrival frontier and hash. Refresh is fenced against live writers and atomically reprojects old histories/minutes across generations. Status compares recorded offsets, sink/progress coverage, immutable hashes, missing admitted events and reference/conflict holds using one database snapshot. It does not query current broker head or establish an atomic transaction across the two readers.

## Real Kafka/Spark check

With shared Kafka and case PostgreSQL healthy and the optional image built, from the repository root:

```sh
python cases/03-payment-reconciliation/streaming/check_live.py
```

The host helper uses Python's standard library and Docker. It ran against a unique diagnostic topic `case03.live-check-93ead548eb8c`, database and checkpoint root, then removed only its own topic/database/checkpoints. Actual live generation: `1a594783-5b71-45f8-90f7-7ccfc7eb8b9f`. It exited zero after all assertions and cleanup.

| Stage | Observed result |
| --- | --- |
| Initial 14 acknowledged deliveries | 11 distinct valid events in both intake and live; two quarantine rows for malformed JSON and an out-of-clock future event; one duplicate suppressed |
| Initial SQL application | Seven applied, one held behind a sequence gap, one illegal capture, two unknown-reference events; gap payment remains initiated, held count 1, captured amount 0 |
| Invalid future clock | The 2099 source timestamp was quarantined before live watermark calculation; valid-source watermark became `2026-10-13T09:52:00.000Z` |
| Live checkpoint restart | All four business-table fingerprints unchanged, including admitted-event first-commit timestamps |
| Missing sequence arrives | Gap payment becomes captured, held count 0, captured amount 4,200 minor units; old minute bucket rebuilt |
| Cross-batch re-publication | Two more duplicate deliveries; all live ledger/projection fingerprints unchanged (A9) |
| Independent reference refresh | M041's two events move from unknown-reference to applied; previously stored minutes are revised; recorded-frontier consistency becomes true |
| Metric grain | At 10:01 UTC, M001/GBP has two authorisations and one decline: 33.3333…%. M041/GBP has one authorisation, no declines: 0%. At 10:02, expiry count is 1 and decision rate is NULL |
| Valid event-time advance | Watermark reaches `2026-10-13T10:20:00.000Z`; Spark reports 12 state rows removed, with one remaining |
| Later event behind observed watermark | Intake retains it; live excludes it; Spark reports one `numRowsDroppedByWatermark`; status exposes one missing admitted event and becomes false |

Final isolated counts: **19 physical deliveries, 14 canonical intake events, two quarantine rows and 13 admitted live events**. Live application: 12 applied, one illegal. Both recorded frontiers agree: partition 0 = 1, partition 1 = 6, partition 2 = 12 (exclusive next offsets); progress covers sink commits and no immutable conflict exists. The remaining late event still requires controlled repair, so equal offsets alone do not make the output complete.

Observed deduplication state rows: 11 → 11 → 12 → 12 → 13 → 1 → 1. Duplicate-drop custom metrics total three; watermark-drop count totals one. Maximum reported state-operator `memoryUsedBytes` in these progress records was 4,816 bytes. This is a tiny operator observation, not a total JVM-memory measurement or sustained state-bound benchmark. A12 remains open.

The helper now writes full progress diagnostics to ignored `evidence/ci/live-integration.json` when run; the extended workflow is configured to upload that report. The recorded local check above used console snapshots before the report-writing addition; remote report upload is not yet verified.

## Database and compatibility checks

From the case directory:

```sh
docker compose run --rm --no-deps -e PAYRECON_REQUIRE_DB=1 --entrypoint pytest payrecon -q --junitxml=evidence/ci/live-pytest.xml
```

**78 passed in 554.83 seconds, zero failures/errors/skips.** This ran alongside the real Spark phases, so its elapsed time is not a comparable performance benchmark. Existing independent batch replay, reconciliation and rerun assertions passed after extracting shared SQL. New tests compare every payment-state business column with batch on the same fixture, including over-refund rejection and reversal amounts. Other cases cover reference refresh, generation isolation, exact callback replay, immutable conflicts, transaction rollback after projection failure, writer fencing, currency/merchant identity and explicit provisional status.

After tightening progress coverage and snapshot reads, reran all ten live database tests: **10 passed in 13.88 seconds**. The first focused run caught an incorrect test expectation that pooled M001 and M041's decisions; corrected it to the declared per-merchant grain, then the focused/full runs passed.

Built the optional locked image successfully; build actually started Spark 4.2.1 with the matching connector. Runtime versions are unchanged from [intake evidence](stream-intake-run.md). Ran the additive `stream_002_live` migration against the retained intake-only demo and resumed its original intake checkpoint successfully, with generation `65d538d3-4f93-4cae-a175-1bfe1bafb6ca` unchanged. This verifies compatibility with an older sidecar that has no explicit role; it was not silently replaced or reset.

Ruff lint and formatting passed (73 files formatted); base and streaming-overlay Compose configurations validated; `actionlint` passed for the extended workflow. Git whitespace checks passed and all 121 relative Markdown links in changed/new documents and local handoff files resolved. Both final images built successfully. Image-only probes resolved shared SQL includes and imported the live modules/helper; the batch image had no Spark/Kafka producer/JRE dependencies and CLI help worked with Kafka stopped. The [run guide](../streaming/README.md) owns executable setup, upgrade, reference and status commands.

After the migration/restart check, acknowledged one resend of the original eight-delivery fixture into its existing diagnostic topic. Resumed the original intake checkpoint: eight new transport rows, zero new canonical events, four duplicates and four quarantine rows. Loaded the independent initial reference snapshot (40 merchants), then created a distinct live checkpoint/generation `053c312a-efe3-4eb4-8a48-4699689ca334`. All commands exited zero.

Retained `payments_stream` state: 16 raw deliveries, three canonical intake events, eight quarantine rows, three admitted/applied live events and two query lineages. Canonical intake digest stayed `1f27ca689a124e180370e8a6b0cea38b`, including unchanged first-commit metadata. Payment `pay-9000002` is captured, held count 0, captured amount 4,200 EUR minor units. Both recorded frontiers are partition 0 = 0, partition 1 = 2, partition 2 = 14, with progress covering sink commits and consistency true at that frontier. The day-late source event was admitted in this initial live batch; source ingestion lag is not evidence of a Spark watermark drop.

The primary `case03.payment-events.v1` topic remains empty; the retained demo uses `case03.intake-check`. Isolated live-check resources were removed. Shared Kafka is stopped at handoff with its data/network and both case checkpoint directories preserved. PostgreSQL remains running; original batch/Airflow databases and stopped Airflow state are preserved.

## Remaining work

The live slice is a local driver-to-PostgreSQL sink with a bounded historical fixture. Reported progress may lag commits, the broker may have newer data, and checkpoints remain separate from database durability. No end-to-end exactly-once, cloud deployment, backup/archive, broker-loss recovery or benchmark claim is made.

Next: commit/push this slice and confirm the extended live workflow; then inject consumer failures before/after sink commit, implement controlled late repair with a fenced writer and explicit intake/reference frontier, prove parity, and test repeated watermark eviction. No new business decision blocks that sequence.
