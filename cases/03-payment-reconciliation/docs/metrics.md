# Metric catalogue

Status: 12 November 2026. The financial control components and exception ages are stored by the batch reconciliation. Match-rate, fee-variance and authorisation-age summaries are consumption definitions, not separate implemented reports. Streaming applied-event counts, decline rate, separate expiries and application diagnostics are implemented in SQL, with controlled repair audit and recorded-frontier status. Latency/throughput distributions and broker-head backlog remain future work. This is the authoritative metric catalogue.

All money is in minor units of one currency; no metric adds currencies. Dates are UTC.

## Finance (batch, per run)

Owner persona: finance reconciliation analyst. Every result is selected for an explicit `as_of_arrival`; amounts from different runs must not be added. Counts are payments or lines as stated, money is signed integer minor units, and rates are percentages when displayed. A zero-denominator rate is NULL (displayed as unavailable), rather than zero.

| Metric | Definition | Grain | Notes |
| --- | --- | --- | --- |
| Settlement match rate | Matched payments whose grace period has ended ÷ all payments whose grace period has ended, within the same run, currency and capture-date group | Currency × capture date | Both numerator and denominator use `last_due_date < as_of_arrival - 1`; a matched payment still in grace is excluded too. Return NULL when no payment is eligible |
| Open exception value | Sum of the signed gross differences (expected − settled) of open exceptions | Currency × due date × exception type | Explains the control-total difference exactly |
| Pending value | Expected minus settled gross of payments classified as pending within their grace period | Currency × due date | Outstanding within grace; not an exception |
| Awaiting value | Minus the gross of settlement lines with no matchable payment, still within their grace day | Currency × settlement date | Not an exception yet: the payment may still be held or its merchant unknown ([decision 0005](decisions/0005-reconciliation-runs-and-grace.md)) |
| Unexplained difference | Captured − refunded − reversed − settled gross − open exception value − pending value − awaiting value | Currency × due date | Must be 0. Anything else is a defect in the reconciliation |
| Fee variance | Settled sale-line fee − expected fee from the price plan, for payments with sale lines, grouped within a selected run | Currency × merchant × capture date | Refund/reversal-only cases have no sale fee to compare. Exceptions follow decision 0005: gross must agree before a fee mismatch is classified |
| Exception age | `max(0, processed_through - (last_due_date + 1))` for payment exceptions; use settlement date instead of last due date for unmatched lines. `processed_through = as_of_arrival - 1` | Exception | Stored in `recon.exception.age_days`; the first day beyond grace has age 0. Ageing buckets: 0–2, 3–7, over 7 days |
| Ageing authorisations | Payments authorised but neither captured, voided nor expired after 7 days | Currency × merchant | Not a settlement exception; an operational follow-up |

**Example.** On 3 March, £1,000.00 is captured in GBP and £100.00 refunded. The GBP file for 3 March settles £880.00 gross. One payment of £20.00 is still pending in its grace day, and there are no exceptions: unexplained difference = 100000 − 10000 − 0 − 88000 − 0 − 2000 − 0 = 0 pence.

**Due date.** A payment's amounts and the lines matched to it are counted under its capture date; a line that matches no payment is counted under its own settlement date. Implemented in `sql/recon/reconcile.sql`, table `recon.control_total`.

**Grace versus grouping.** `due_date` is the capture date used for financial grouping. `last_due_date` is the latest capture/refund/reversal date used to decide grace. A later refund can put an older capture-date group back within grace; it does not move the payment's control-total bucket.

## Operations (streaming, milestone 2)

Owner persona: payment operations analyst. Owner confirmed 12 November 2026: use an authorisation decision decline rate and show expiries separately. The former `(declined + expired) / initiated` in an event-time minute mixed unrelated initiation/decision cohorts and is replaced. Select one live generation; count distinct, schema-valid events that the SQL state walk actually applied. Held, illegal, unknown-reference and inconsistent merchant/currency identities appear as diagnostics and are excluded from the decision-rate denominator. Counts are event counts, rather than current payment-state snapshots. A zero denominator gives NULL; missing event counts are zero. Sum eligible numerator/denominator counts when rolling up, rather than averaging percentages.

| Metric | Definition | Grain | Notes |
| --- | --- | --- | --- |
| Authorisation decline rate | Applied declined events ÷ (applied authorised + applied declined events) | Currency × merchant × one-minute decision-event-time window | Report as a percentage; both sides use the same window. Event-ID uniqueness prevents replay inflation |
| Expiry count | Applied expired events | Currency × merchant × one-minute expiry-event-time window | Separate operational count; never added to the authorisation decline-rate numerator |
| Event counts | Applied unique events by event type; held/illegal/unknown-reference counts shown separately | Currency × merchant × one-minute event-time window × event type | Replaced from the admitted ledger after retry, reference refresh or repair; not incremented blindly |
| Throughput | Physical Kafka deliveries read per wall-clock second, and unique applied events per wall-clock second, labelled separately | Declared processing interval | Measure both readers separately; duplicate reads are not extra business events |
| Broker-to-sink latency | First successful sink commit time − broker append timestamp, p50 and p95 | Unique event / specified query | Separate intake and live results. Retries do not replace the first successful commit time |
| Event-to-sink latency | First successful live commit time − event time, p50 and p95 | Unique applied event | Use a paced current-clock/rebased fixture for a real-time claim; historical source timestamps measure event age, not processing speed |
| Source late-event count | Unique schema-valid intake events with source ingestion time − event time > 10 minutes | Dataset × currency × merchant | A source-clock diagnostic, not a claim that Spark dropped those records |
| Spark watermark drops | Spark's reported `numRowsDroppedByWatermark`, alongside actual watermark and input offsets | Live query generation × micro-batch | Physical operator metric; not a unique business-event count. Events older than the delay can be admitted or excluded; durable intake is the repair source |
| Backlog/readiness | Target offsets minus completed query frontiers, reported per partition and query | Topic epoch × partition × query role | No sum across different epochs. Published view is provisional until both readers reach the stated frontier and conflict/reference checks pass |

The [streaming design](streaming-design.md) owns admission, checkpoint and repair policy. Ten minutes is the confirmed watermark delay, not a measured latency target or a guarantee that every older event is discarded. Backfill can revise previously incomplete minute counts and payment state; expose the repair ID and generation.

Implemented consumption: `stream.event_minute` stores counts by generation/minute/merchant/currency/event type/outcome; `stream.operation_minute` exposes percentage decline rate, separate expiry count and diagnostics at generation/minute/merchant/currency grain. `stream-status` reports recorded intake/live offsets, progress coverage of database commits, missing or unverified events, conflicts, reference frontier and latest repair ID/scope. Its consistency flag uses a single database snapshot and does not query the broker head; absolute backlog against an externally selected target remains unimplemented. See the [run guide](../streaming/README.md).
