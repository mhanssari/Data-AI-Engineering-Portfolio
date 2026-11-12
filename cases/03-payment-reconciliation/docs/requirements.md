# Requirements

Status: agreed 12 November 2026. Milestone 1 (batch) is validated: clean checkout, landing/core/reconciliation, shared Airflow recovery/rerun and GitHub database CI. The full case remains in progress; streaming and failure/measurement milestones are outstanding. See [batch evidence](../evidence/batch-clean-setup-run.md).

## Problem and decisions

A fictional payment provider takes card and wallet payments for merchants in pounds, euros and dollars. Its acquirer sends a settlement file each day. Two teams need different things from the same payments, at different speeds.

| Who | Decision | What they need | Cadence |
| --- | --- | --- | --- |
| Payment operations | Is something failing right now, and for which merchants? | Failure and decline rates by minute, by merchant and currency, with late and duplicate events handled | Streaming, minutes |
| Finance | Has every captured payment been settled correctly, and what is still outstanding? | Payments matched to settlement lines; every difference explained by an exception; control totals per currency and day | Batch, daily |

Streaming gives early warning; the daily batch establishes completeness. Neither replaces the other.

The design question: **how do you keep an operational view fast and a financial view exact when the same payments arrive duplicated, out of order and late?**

## Scope

**Milestone 1, batch:**
- **Sources:** synthetic payment events and daily settlement files from a seeded generator.
- **Landing:** append-only, with file checks and quarantine.
- **Payment state:** derived from events using the source sequence.
- **Reconciliation in PostgreSQL:** matching, exceptions and control totals.
- **Orchestration:** a daily Airflow DAG in the shared portfolio Airflow ([decision 0001](decisions/0001-shared-airflow.md)).

**Milestone 2, streaming:**
- **Pipeline:** the same events through Kafka (KRaft) and Spark Structured Streaming into PostgreSQL.
- **Event handling:** deduplication, an event-time watermark, and an idempotent sink.
- **Outputs:** minute aggregates and a simple operational view.

The [streaming design](streaming-design.md) owns the event contract, proposed output grains, checkpoint/backfill boundaries and runtime candidates. Only the shared broker and case topic initializer are implemented and locally validated so far. Owner confirmed 12 November 2026: ten-minute event-time watermark delay; authorisation decline rate with expiries shown separately. Shared Kafka topology is recorded in [portfolio decision 0002](../../../platform/decisions/0002-shared-kafka-topology.md). These choices do not alter the validated batch settlement rules.

**Milestone 3, failure and measurement:**
- **Injected failures:** a consumer crash and a poison event.
- **Recovery:** replay, recovery from a checkpoint, and a controlled backfill beyond the lateness window.
- **Measurement:** latency and throughput on this laptop.

**Out of scope:** fraud scoring, real money movement, foreign exchange (no amount is ever converted), chargebacks and disputes, multi-region resilience, any cloud deployment. Databricks is an optional later extension.

## Settlement rules

Agreed defaults, to be revisited with a finance reviewer:

| Rule | Definition |
| --- | --- |
| Match key | The settlement line's payment reference is the payment id |
| Money | Integer minor units (pence, cents) with an ISO currency code; amounts in different currencies are never added |
| Expected settlement | Per payment: captured amount minus refunded amount, in the payment's currency; a reversal takes it to zero |
| Fee | Settlement net = gross − fee. The expected fee comes from the merchant's price plan (basis points plus a fixed amount, rounded half up to a minor unit); a difference above one minor unit is a fee exception |
| Partial settlement | A payment may settle over several lines; their gross amounts are summed |
| Refunds and reversals | Negative settlement lines against the original payment |
| Cut-off | A payment captured on day D (UTC, up to 23:59:59) is due in the settlement file for D |
| Grace | One day: until the file for D + 1 has been processed, an unsettled payment is *pending*, not *missing*. D is the payment's last due date: its capture, or its latest refund or reversal |
| Lines that wait | A settlement line that matches no payment yet (the capture is held behind a missing event, or the merchant is not yet in reference data) is *awaiting* for the same one day, then an *unexpected settlement* exception. A line whose payment is due in another currency is a *currency mismatch* at once. Agreed 12 November 2026 ([decision 0005](decisions/0005-reconciliation-runs-and-grace.md)) |
| Run | Identified by its arrival date A; it has processed settlement files up to day A − 1. Rerunning a day replaces that day's results |
| Status authority | The source's per-payment sequence number orders events. Event time is for display and windows; ingestion time is for lateness |

## Acceptance

| # | Criterion |
| --- | --- |
| A1 | Duplicate delivery of events or settlement lines does not change any total |
| A2 | Out-of-order events give the same final payment state as in-order events |
| A3 | An illegal transition (for example a refund before capture) is never applied, and is recorded |
| A4 | A malformed or unknown-reference record is quarantined with its reason and source position; nothing else in its file is lost |
| A5 | Per currency and due date, captured minus refunded minus reversed minus settled equals open exceptions plus payments pending plus lines awaiting: every difference is explained |
| A6 | A payment is pending, and an unmatched line awaiting, until its grace period ends, then an exception; a late file or late reference data resolves it on the next run |
| A7 | Rerunning a day with the same inputs changes nothing |
| A8 | A genuine double settlement is an exception, while a re-sent identical file is not |
| A9 | Duplicate streamed events do not double the minute aggregates (milestone 2) |
| A10 | After a forced consumer failure, restart from the checkpoint loses no accepted event and duplicates none in the sink (milestone 3) |
| A11 | A correction later than the lateness window is recovered by a controlled backfill, and the batch and stream views agree afterwards (milestone 3) |
| A12 | Streaming state stays bounded under the documented watermark and retention (milestone 3) |

Streaming evidence: [A9](../evidence/stream-live-run.md) and [A10/A11](../evidence/stream-recovery-run.md) passed locally and in GitHub CI; [A12](../evidence/stream-state-run.md) passed locally for the declared finite continuous-query profile. This does not prove arbitrary-rate RAM bounds or broker retention expiry. Latency/throughput measurement remains open; the full case is not complete.

## Invariants

Each becomes a test that fails on the planted defects if the handling is removed.

1. Every distinct event is counted once. Distinct events equal the delivered events minus the planted duplicates.
2. A payment's state is the result of applying its events in sequence order through the allowed transitions only.
3. For every currency and due date: captured − refunded − reversed − settled gross = open exception amounts + pending amounts + awaiting amounts.
4. Each settlement line belongs to at most one payment, and a payment's settled gross is the sum of its lines.
5. No amount is ever added across currencies, in SQL or in any output.
6. No payment is *missing*, and no line *unexpected*, before its grace period ends.
7. Every quarantined record keeps its file or offset, its raw content and its reason.
8. Reconciliation results depend only on inputs: the same inputs, run twice, give identical output.

## Definition of done

From the portfolio brief, plus this case's items.

- [x] Batch CLI runs end to end from a clean checkout with documented commands ([evidence](../evidence/batch-clean-setup-run.md))
- [x] Grain of every implemented table recorded ([data model](data-model.md))
- [x] README explains the batch decisions and trade-offs
- [x] Architecture diagram in Mermaid
- [x] Seeded generator with documented defects
- [x] Batch tests cover the invariants, not only schema
- [x] CI builds/tests case changes on push; pull-request triggering configured (first push run and artifact verified)
- [x] Runtime credentials outside Git; committed examples and isolated CI use dummy values
- [x] Batch limitations and "what I would do differently at scale"
- [x] Batch reconciliation orchestrated by the shared Airflow, with a late-file rerun recorded (A6): [evidence](../evidence/airflow-late-file-run.md)
- [x] Small-fixture streaming correctness with replay/recovery and declared state profile (A9–A12); A12's first remote run remains pending
- [ ] Measured latency and throughput, with method and hardware stated

## What I would do differently at scale

Drafted now, refined at the end.

- **Partitioning:** partition events by payment id in Kafka, so one payment's events stay ordered within a partition, and partition PostgreSQL tables by settlement date.
- **State:** keep streaming state in a managed store with a time-to-live, instead of relying on a laptop's checkpoint folder.
- **Settlement files:** land them in object storage with an arrival notification, rather than having a sensor watch a folder.
- **Core and reconciliation:** update core incrementally per arrival and reconcile per settlement day, instead of rebuilding over the whole history ([decision 0004](decisions/0004-core-rebuilt-as-of-arrival.md)); rerun safety then needs explicit undo of a day's effects.
