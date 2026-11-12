# Metric catalogue

Status: 12 November 2026. The financial control components and exception ages are stored by the batch reconciliation. Match-rate, fee-variance and authorisation-age summaries are consumption definitions, not separate implemented reports. Streaming metrics remain proposed for milestone 2. This is the authoritative metric catalogue.

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

| Metric | Definition | Grain | Notes |
| --- | --- | --- | --- |
| Failure rate | Declined + expired ÷ payments initiated | Currency × merchant × one-minute event-time window | Distinct payments; duplicate events do not count twice |
| Throughput | Distinct events processed per second | Processing time | Measured, not targeted |
| End-to-end latency | Sink write time − event time, p50 and p95 | Event | Reported with the hardware and load used |
| Late events | Events whose event time is behind the watermark when they arrive | Window | Dropped from the stream view by design; recovered in batch |
