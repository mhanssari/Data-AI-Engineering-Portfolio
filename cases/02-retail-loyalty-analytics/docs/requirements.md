# Requirements

Status: built and validated locally, 12 November 2026. Design agreed 11 November 2026; the evidence for each criterion is linked below.

## Problem and decisions

A fictional consumer-goods retailer runs a loyalty programme across a dozen stores. Marketing, store operations and finance each count "active customers", "members" and "redemptions" differently, so the same question gets three answers.

| Who | Decision | What they need |
| --- | --- | --- |
| Loyalty marketing | Which enrolment cohorts and members to re-engage | Repeat-purchase and retention by cohort, with immature cohorts kept apart; members who have gone inactive, including those who never bought |
| Store operations | Which stores and reward types are underperforming | Net sales per trading day per store, comparable across stores that opened at different times; redemption by reward type with a defensible denominator |
| Finance | Whether reported sales can be trusted | Net sales after returns that reconcile to the transaction ledger, whichever date basis a report uses |

The design question: **which definitions and model grains make these numbers agree, and stay agreed when late data arrives?**

## Scope

**In scope:** a seeded generator for two batches of source extracts; an append-only load into a local warehouse; dbt staging, intermediate and mart models; one incremental fact; one history dimension built from source history; one dbt snapshot of what the warehouse knew; cohort, retention and redemption marts; data quality through source freshness, enforced model contracts, schema, unit and business-rule tests with stored failures and a recorded history of every result; incremental against full-refresh parity; CI; an opt-in Airflow DAG that runs the same steps per batch; a Power BI report of two pages over the marts.

**Out of scope:** campaign attribution, marketing activation, predictive churn, points-balance accounting, personal data of any kind, multiple currencies, a live Snowflake deployment (an opt-in target only, see [decision 0001](decisions/0001-warehouse-target.md)).

## Acceptance

Each line becomes a test or a recorded check. Details of the planted defects are in the [data model](data-model.md#planted-defects).

| # | Criterion | Evidence |
| --- | --- | --- |
| A1 | Returns reduce net sales on both date bases, and the two bases agree in total | `assert_net_sales_match_the_source_control_total`, `assert_returns_are_attributed_to_their_original_sale`; planted D4 in `test_sales_facts.py` |
| A2 | Several loyalty events on one purchase never multiply revenue | `assert_marts_never_change_revenue`; planted D3 in `test_loyalty_marts.py` |
| A3 | Inactive-member logic counts members who never purchased | Unit test `a_member_who_never_bought_becomes_eligible_and_inactive`; planted D8 in `test_member_marts.py` |
| A4 | Immature cohorts are reported as immature, not as 0% | `assert_immature_cohorts_are_never_reported_as_failed`; July 2026 cohort maturing between batches; Power BI blank checks |
| A5 | Loading the same batch twice lands nothing; rebuilding changes nothing | `test_loader.py`; `test_a_rerun_with_nothing_new_changes_nothing` |
| A6 | After a late correction, an incremental build equals a full refresh | `test_sales_facts.py`: after batch 2, a late store version and a late sale ([decision 0006](decisions/0006-incremental-boundary.md)) |
| A7 | Sales are attributed to the store version in force on the transaction date | `assert_every_sales_line_has_the_store_version_in_force`; planted D9 |
| A8 | A product sold before its reference data arrives is kept, then completed | Planted D10 in `test_dimensions.py`; unit test on `dim_product` |
| A9 | Reward status is resolved from the event log when the status column disagrees | `assert_entitlement_status_follows_the_evidence`; planted D13, D14; unit test on the status rules |
| A10 | Dashboard totals reconcile to reference SQL | [Power BI reconciliation](../evidence/power-bi-reconciliation.md): 34 of 34 |
| A11 | A breaking change fails before merge | Enforced contracts on every mart; a type change rejected in the [run record](../evidence/run-record.md#a-breaking-change-is-rejected-a11) |
| A12 | A corrected member keeps the value reported before the correction | `test_the_snapshot_keeps_what_was_reported_before_a_correction` |
| A13 | A stale extract is reported, not silently used | `test_a_stale_extract_fails_freshness` |
| A14 | Every failing test leaves its failing rows for inspection | `test_a_failing_test_leaves_its_result_and_its_failing_rows` |
| A15 | The Airflow DAG reproduces the default path, and rerunning one batch interval changes nothing | [Airflow backfill run](../evidence/airflow-backfill-run.md) |

## Invariants

Each becomes a singular test named `assert_<what_must_be_true>.sql`.

1. Net sales in `fct_sales_line` equal net sales computed independently from deduplicated source lines.
2. Each transaction's net amount in `fct_transaction` equals the sum of its lines.
3. The two date bases differ only by moving each matched return to its original sale's date, never later than its own; sales and unmatched returns stay on their own date. Totals over the period are therefore equal on both bases.
4. No mart that carries an amount totals to more or less than `fct_sales_line` for the same filter.
5. Store versions for one store never overlap, leave no gaps, and exactly one is current.
6. Every sales line joins to exactly one store version.
7. `fct_member_month` holds exactly one row per member per month from enrolment to the as-of month, whether or not the member bought anything.
8. Cumulative returned quantity never exceeds the quantity sold on the original line; any excess is flagged, not hidden.
9. Every entitlement's resolved status follows the evidence: redeemed if and only if a redemption event exists, expired only after its expiry date, open only before it, and a redemption happens on the member's own transaction within the reward's validity.
10. Repeat-purchase and retention rates are null, never zero, where the window has not closed.

## Definition of done

From the portfolio brief, plus this case's items. Reviewed 12 November 2026.

- [x] Runs end to end from a clean clone with documented commands and no account ([run record](../evidence/run-record.md#from-a-clean-clone))
- [x] The grain of every mart is recorded ([data model](data-model.md))
- [x] README explains the modelling decisions and trade-offs
- [x] Architecture diagram in Mermaid
- [x] Seeded generator with documented defects
- [x] Tests cover the invariants above, not only schema constraints: ten singular tests, seven dbt unit tests, planted outcomes in pytest
- [ ] CI runs build and tests on every pull request: written, linted and path-filtered, **but no run on GitHub has been checked yet**
- [x] No credentials anywhere, including screenshots
- [x] Assumptions and limitations stated
- [x] A "what I would do differently at scale" section
- [x] Technologies described at the right confidence level
- [x] Incremental against full-refresh parity shown after late corrections (A6)
- [x] Power BI report: an executive page and a diagnostic page, with documented measures and reconciliation (A10)
- [x] Snapshot, freshness, contracts and stored failures demonstrated (A11–A14)
- [x] Airflow DAG run recorded, with a backfill over both batches (A15); the default path still needs no Docker

## What I would do differently at scale

Refined at the end of the build.

- **Incremental boundary:** at millions of lines per day, merge by partition (transaction date) instead of by key over the whole table, and bound the late-arrival window explicitly. The load watermark used here assumes a full table scan is cheap.
- **Member-month densification** grows with members × months. At scale it becomes an incremental periodic snapshot, appending each closed month and recomputing only the open one.
- **History:** if the source stopped sending dated store versions, a dbt snapshot over the current-state table would be needed, accepting that it cannot be rebuilt from scratch.
- **Orchestration:** on a warehouse with concurrent writers, Airflow would run independent marts in parallel and trigger on data arrival (asset-aware scheduling) instead of on a timetable.
- **Metric layer:** with several BI tools, definitions would move to a semantic layer instead of being repeated in DAX.
- **Store repair and dependants:** the incremental fact's checks scan every existing key; at scale they would be limited to the partitions the changed rows touch ([decision 0006](decisions/0006-incremental-boundary.md)).
- **BI refresh:** the report reads Parquet files from a folder. A shared report would read a warehouse through a gateway or a direct connection, with scheduled refresh and roles assigned to groups in the Power BI service.
