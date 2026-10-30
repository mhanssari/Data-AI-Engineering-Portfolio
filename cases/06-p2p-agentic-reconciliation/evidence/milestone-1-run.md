# Milestone 1 run record

Measured on 30 October 2026 on the author's Windows 11 machine with Docker Desktop. Synthetic data, seed 6042. Single runs: these show correctness, and the timings are not benchmarks.

**Software:** Python 3.12.16, uv 0.13.6, PostgreSQL 18 with pgvector 0.8.7 (`pgvector/pgvector:0.8.7-pg18-trixie`), Neo4j Community (`neo4j:2026.09.0-community`), psycopg 3.3.6, neo4j driver 6.4.1, rdflib 7.6.0, pySHACL 0.40.2, rapidfuzz 3.14.6, pydantic 2.14.2, typer 0.27.3, pytest 9.2.0, ruff 0.17.3.

## Commands and outcomes

| Command | Outcome |
| --- | --- |
| `docker compose ... down -v`, then the README's steps from empty volumes, ending in `uv run p2p run-all` | Exit 0, about 30 seconds |
| `uv run p2p reset --yes`, then `uv run p2p run-all`, on running containers | Exit 0, about 12 seconds |
| `uv run p2p dq` | 36 pass, 0 warn, 0 fail |
| `uv run p2p graph-sync` | 3,395 nodes, 6,862 edges, no reconciliation failures |
| `uv run pytest` | 67 passed, 21 to 25 seconds |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 53 files already formatted |

After batch 1 alone, one data-quality warning was expected and raised: 7 rows in open quarantine, the S20 chain waiting for its supplier. They cleared in batch 2.

## Volumes in core

| Requisitions | Purchase orders | Receipt lines | Invoices and credit notes | Invoice lines | Quarantined at some point | Open quarantine |
| --- | --- | --- | --- | --- | --- | --- |
| 400 | 317 | 326 | 307 | 360 | 7 | 0 |

Raw holds more rows than core on purpose: for example 402 requisition rows for 400 requisitions, because two CDC deletes landed as further versions.

## Matching results after both batches

| Status | Invoice lines |
| --- | --- |
| Matched | 307 |
| MatchedWithinTolerance | 31 |
| Exception | 21 |
| PendingReceipt | 1 |

| Scope | Value on hold (Exception and PendingReceipt, net) |
| --- | --- |
| DE01 EUR | 34,450.00 |
| DE01 GBP | 1,000.00 |
| GB01 GBP | 18,965.00 |

| Scope | GRNI at 30 September 2026 | PO lines |
| --- | --- | --- |
| DE01 EUR | 90,782.00 | 18 |
| GB01 GBP | 79,523.90 | 18 |

These are not summed across currencies. They are synthetic and say nothing about a real business.

## Do the tests have teeth?

A throwaway script, not kept in the repository, broke one rule at a time and ran the non-graph tests, restoring the file each time.

- First pass: 9 of 12 mutations caught. One survivor was redundancy: stale rows are blocked by two independent layers, and removing both is caught. Two were real gaps: no scenario had a genuine invoice after a held duplicate, or a first-ever bank account inside the window. Both became scenarios.
- Second pass, extended: 17 of 20 caught. Three more gaps became scenarios: a rejected credit note in GRNI, a split group dissolved by a later cancellation, and a one-penny rounding match.
- Final pass: 20 of 20 caught.

| Mutation | Caught |
| --- | --- |
| Price tolerance ignored | Yes |
| Contract tolerance ignored | Yes |
| Approval limit read as of today, not the approval date | Yes |
| No FX conversion on the approval limit | Yes |
| Duplicates matched on source supplier, not golden | Yes |
| Held duplicates consume quantity | Yes |
| First bank account counted as a change | Yes |
| Oracle SUBMIT treated as an approval | Yes |
| SAP credit-memo sign lost | Yes |
| No watermark lookback | Yes |
| Stale row wins (both layers removed) | Yes |
| Held duplicate reduces GRNI | Yes |
| Rejected credit note reduces GRNI | Yes |
| GRNI ignores the cut-off | Yes |
| Control breach not propagated to invoices | Yes |
| Split rule disabled | Yes |
| Deleted requisition still assessed | Yes |
| Quarantine never retried | Yes |
| VAT conflict can auto-merge | Yes |
| Rounding tolerance ignored | Yes |

## Failure and recovery shown by tests

| Injected failure | Result | Test |
| --- | --- | --- |
| Delivery shorter than its manifest declares | Whole batch rolled back, nothing landed, run recorded as failed | `test_bad_delivery_fails_atomically_and_is_recorded` |
| Corrupted file (checksum differs) | Same | Same |
| Renamed column in a file | Rejected before landing | `test_schema_drift_is_rejected` |
| Row older than the watermark lookback | Conform misses it by design; the row-count reconciliation fails and says so | `test_row_older_than_the_lookback_is_caught_by_reconciliation` |
| Core amount changed outside the pipeline | Control total and header-to-line check both fail | `test_control_total_detects_a_core_amount_changed_outside_the_pipeline` |
| Supplier master arrives after its documents | 7 rows quarantined, then cleared in a single later run | `test_quarantine_opened_and_cleared_when_the_parent_arrives` |
| Duplicate delivery and stale out-of-order rows | No effect on core or results | `test_rerun_produces_identical_results`, `test_stale_out_of_order_row_does_not_overwrite` |
