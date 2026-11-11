# Requirements

Status: agreed design, 11 November 2026. Nothing is implemented yet; see the case README for progress.

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

| # | Criterion | How it is checked |
| --- | --- | --- |
| A1 | Returns reduce net sales on both date bases, and the two bases agree in total | Singular test against a control total from deduplicated source rows |
| A2 | Several loyalty events on one purchase never multiply revenue | Singular test: every mart amount reconciles to `fct_sales_line` |
| A3 | Inactive-member logic counts members who never purchased | dbt unit test plus a planted member |
| A4 | Immature cohorts are reported as immature, not as 0% | Singular test on cohort marts; planted late enrolments |
| A5 | Loading the same batch twice lands nothing; rebuilding changes nothing | pytest |
| A6 | After a late correction, an incremental build equals a full refresh | pytest comparing every mart both ways |
| A7 | Sales are attributed to the store version in force on the transaction date | Singular test plus a planted format change |
| A8 | A product sold before its reference data arrives is kept, then completed | Planted product; inferred row replaced after batch 2 |
| A9 | Reward status is resolved from the event log when the status column disagrees | Singular test plus planted entitlements |
| A10 | Dashboard totals reconcile to reference SQL | Recorded comparison in `evidence/` |
| A11 | A breaking change fails CI before merge | Enforced contracts on marts; demonstrated once in the PR history |
| A12 | A corrected member keeps the value reported before the correction | `snp_member` holds both versions after batch 2 |
| A13 | A stale extract is reported, not silently used | Source freshness warns and errors against set thresholds |
| A14 | Every failing test leaves its failing rows for inspection | `store_failures` audit schema and the `audit.dq_test_result` history |
| A15 | The Airflow DAG reproduces the default path, and rerunning one batch interval changes nothing | Recorded run in `evidence/`; marts compared with the CLI run |

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
9. Every entitlement is in exactly one resolved state, and redeemed, expired, open and cancelled sum to issued.
10. Repeat-purchase and retention rates are null, never zero, where the window has not closed.

## Definition of done

From the portfolio brief, plus this case's items:

- [ ] Runs end to end from a clean clone with documented commands and no account
- [ ] The grain of every mart is recorded ([data model](data-model.md))
- [ ] README explains the modelling decisions and trade-offs
- [ ] Architecture diagram in Mermaid
- [ ] Seeded generator with documented defects
- [ ] Tests cover the invariants above, not only schema constraints
- [ ] CI runs build and tests on every pull request
- [ ] No credentials anywhere, including screenshots
- [ ] Assumptions and limitations stated
- [ ] A "what I would do differently at scale" section
- [ ] Technologies described at the right confidence level
- [ ] Incremental against full-refresh parity shown after late corrections (A6)
- [ ] Power BI report: an executive page and a diagnostic page, with documented measures and reconciliation (A10)
- [ ] Snapshot, freshness, contracts and stored failures demonstrated (A11–A14)
- [ ] Airflow DAG run recorded, with a backfill over both batches (A15); the default path still needs no Docker

## What I would do differently at scale

Drafted now, refined at the end.

- **Incremental boundary:** at millions of lines per day, merge by partition (transaction date) instead of by key over the whole table, and bound the late-arrival window explicitly. The load watermark used here assumes a full table scan is cheap.
- **Member-month densification** grows with members × months. At scale it becomes an incremental periodic snapshot, appending each closed month and recomputing only the open one.
- **History:** if the source stopped sending dated store versions, a dbt snapshot over the current-state table would be needed, accepting that it cannot be rebuilt from scratch.
- **Orchestration:** on a warehouse with concurrent writers, Airflow would run independent marts in parallel and trigger on data arrival (asset-aware scheduling) instead of on a timetable.
- **Metric layer:** with several BI tools, definitions would move to a semantic layer instead of being repeated in DAX.
