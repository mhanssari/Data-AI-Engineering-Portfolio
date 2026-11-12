# Metric catalogue

Status: proposed, 12 November 2026. The one authoritative definition of each metric; code links here.

All money is in minor units of one currency; no metric adds currencies. Dates are UTC.

## Finance (batch, per run)

| Metric | Definition | Grain | Notes |
| --- | --- | --- | --- |
| Settlement match rate | Payments due and *matched* ÷ payments due whose grace period has ended | Currency × capture date | Pending payments are excluded, so a young day never reads as failing |
| Open exception value | Sum of the signed amounts of open exceptions | Currency × settlement date × exception type | Explains the control-total difference exactly |
| Pending value | Expected settlement of payments still within their grace period | Currency × settlement date | Not an exception: not yet due |
| Unexplained difference | Captured − refunded − settled gross − open exception value − pending value | Currency × settlement date | Must be 0. Anything else is a defect in the reconciliation |
| Fee variance | Settled fee − expected fee from the price plan | Currency × merchant × day | Exceptions only above one minor unit |
| Exception age | Days from the end of grace to the run date | Exception | For ageing buckets: 0–2, 3–7, over 7 days |
| Ageing authorisations | Payments authorised but neither captured, voided nor expired after 7 days | Currency × merchant | Not a settlement exception; an operational follow-up |

**Example.** On 3 March, £1,000.00 is captured in GBP and £100.00 refunded. The GBP file for 3 March settles £880.00 gross. One payment of £20.00 is still pending in its grace day, and there are no exceptions: unexplained difference = 100000 − 10000 − 88000 − 0 − 2000 = 0 pence.

## Operations (streaming, milestone 2)

| Metric | Definition | Grain | Notes |
| --- | --- | --- | --- |
| Failure rate | Declined + expired ÷ payments initiated | Currency × merchant × one-minute event-time window | Distinct payments; duplicate events do not count twice |
| Throughput | Distinct events processed per second | Processing time | Measured, not targeted |
| End-to-end latency | Sink write time − event time, p50 and p95 | Event | Reported with the hardware and load used |
| Late events | Events whose event time is behind the watermark when they arrive | Window | Dropped from the stream view by design; recovered in batch |
