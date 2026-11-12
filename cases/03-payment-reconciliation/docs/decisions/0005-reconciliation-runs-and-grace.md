# 0005 Reconciliation runs, the grace period, and lines that wait

Status: accepted

Date: 12 November 2026

## Context

Finance needs, for every day, what was settled correctly, what is still outstanding, and an exception for every difference, with no difference left unexplained (A5, invariant 3). Settlement files and payment events can both be late: a sale line can arrive before its payment can be matched, because the capture is held behind a missing event (P2) or the merchant is not yet in reference data (P5). Raising an exception at once for those would be noise; never raising one would hide a real orphan (P7).

## Options

1. **Exception at once** for any line without a matchable payment, resolved by a later run. Simple, but most such exceptions are noise that clears the next day.
2. **Wait indefinitely** while there is any chance the payment will appear. Quiet, but a genuine orphan is never reported.
3. **The same one-day grace for lines as for payments**, then an exception. Agreed with the case owner on 12 November 2026.

## Decision and rationale

Option 3, with these rules (implemented in `sql/recon/reconcile.sql`):

- **Run identity.** A run is identified by its arrival date A. It has processed settlement files up to day R = A − 1, because each day's file arrives the next day. A rerun of a day replaces that day's results; `ops.run` logs every execution.
- **Grace.** Something due on day D is within grace while D ≥ R, that is until the file for D + 1 has been processed. For a payment, D is its last due date: the capture date, or the date of its latest refund or reversal.
- **Payments due** (every payment that was captured) are classified in this order:
  1. *matched*: settled gross in the payment's currency equals captured − refunded − reversed, and the sale lines' fee is within one minor unit of the price plan;
  2. *fee mismatch*: gross agrees but the fee does not; raised at once, since no further line can change it;
  3. *pending*: any other difference while within grace;
  4. after grace: *currency mismatch* if a line for it arrived in another currency; *missing settlement* if no line arrived; *duplicate settlement* if two or more sale lines each settle the full captured amount; otherwise *amount mismatch*.
- **Lines that match no payment due in their currency:**
  - *currency mismatch* at once, if the payment is due in another currency;
  - otherwise *awaiting* while within grace;
  - otherwise an *unexpected settlement* exception.
- **Control totals** per currency and due date: captured − refunded − reversed − settled gross = pending + awaiting + open exceptions. The left side is computed from core, independently of the classification, so a payment or line that the classification lost or double-counted appears as an unexplained difference. Matched lines are bucketed under their payment's due date, unmatched lines under their own settlement date.
- **Checks in the same transaction:** unexplained difference is zero (invariant 3); nothing is missing within grace (invariant 6); every payment due is classified once; every line counts towards exactly one payment or is unmatched (invariant 4). Any violation fails the run, rolls back its results and records the failure in `ops.run`.

## Consequences and validation

- **Reference data later than grace still raises an exception.** P5's merchant arrives two days after its events: its line is *unexpected* on the run for 11 October and resolved on 12 October. This is deliberate: finance sees the gap, and it clears itself.
- **A late file shows as missing, then resolves.** P14 makes 473 USD payments *missing* on 22 October, with every one *matched* on the 23rd. Refund lines in that file make 24 older payments look over-settled (*amount mismatch*) for the same day.
- **Exceptions are a per-run work list, not a case log.** "Resolved" means present in one run and absent in the next. Exception age is counted from the end of grace, so it is a function of the run's inputs.
- **Tests:** every planted outcome on the runs where it should appear; exactly the planted exceptions on the last run; rerunning 22 October gives identical rows; each check reports a deliberately corrupted result; a failed run publishes nothing.
- **Revisit with a finance reviewer:** the one-day grace, the one-minor-unit fee tolerance, and whether a missing refund line deserves its own exception type rather than *amount mismatch*.
