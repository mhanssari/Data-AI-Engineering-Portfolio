-- Reconcile as of one arrival date, after core has been built as of the same date (payrecon.recon.reconcile).
-- The caller sets payrecon.as_of_arrival for the transaction. Rules: docs/requirements.md, decision 0005.
--
-- A run as of arrival A has processed the settlement files up to day A - 1 (each day's file arrives the next day).
-- Call that day R. Something due on day D is in its grace period while D >= R: until the file for D + 1 has been
-- processed, an unsettled payment is pending and an unmatched line is awaiting. After that, an exception.

create temporary table run on commit drop as
select current_setting('payrecon.as_of_arrival')::date as as_of_arrival,
       current_setting('payrecon.as_of_arrival')::date - 1 as processed_through;

-- A rerun replaces the day's results.
delete from recon.exception where as_of_arrival = (select as_of_arrival from run);
delete from recon.control_total where as_of_arrival = (select as_of_arrival from run);
delete from recon.unmatched_line where as_of_arrival = (select as_of_arrival from run);
delete from recon.payment_settlement where as_of_arrival = (select as_of_arrival from run);

-- Payments due: every payment that was captured, whatever happened after. Lines count towards a payment only in
-- its own currency; a line in another currency is counted, never added (invariant 5).
create temporary table due on commit drop as
select p.payment_id, p.merchant_id, p.currency, p.captured_at::date as due_date,
       p.status_changed_at::date as last_due_date, p.captured_minor, p.refunded_minor, p.reversed_minor,
       p.expected_settlement_minor as expected_minor,
       (p.captured_minor * m.fee_bps + 5000) / 10000 + m.fee_fixed_minor as expected_fee_minor
from core.payment p
join core.merchant m using (merchant_id)
where p.captured_at is not null;

insert into recon.payment_settlement
select run.as_of_arrival, d.payment_id, d.merchant_id, d.currency, d.due_date, d.last_due_date,
       d.captured_minor, d.refunded_minor, d.reversed_minor, d.expected_minor,
       s.settled_gross_minor, s.settlement_lines, s.sale_lines_at_full_amount, d.expected_fee_minor,
       s.settled_fee_minor, s.foreign_currency_lines,
       case
           when d.expected_minor = s.settled_gross_minor
                and (s.sale_lines = 0 or abs(s.settled_fee_minor - d.expected_fee_minor) <= 1) then 'matched'
           -- Gross agrees, so nothing more is coming: a fee outside tolerance is an exception at once.
           when d.expected_minor = s.settled_gross_minor then 'fee_mismatch'
           when d.last_due_date >= run.processed_through then 'pending'
           when s.foreign_currency_lines > 0 then 'currency_mismatch'
           when s.settlement_lines = 0 then 'missing_settlement'
           -- Two sale lines that each settle the full captured amount: a genuine double settlement (P13), as
           -- opposed to partial lines that together fall short or overshoot.
           when s.sale_lines_at_full_amount >= 2 then 'duplicate_settlement'
           else 'amount_mismatch'
       end
from due d
cross join run
cross join lateral (
    select coalesce(sum(l.gross_minor) filter (where l.currency = d.currency), 0) as settled_gross_minor,
           count(*) filter (where l.currency = d.currency) as settlement_lines,
           count(*) filter (where l.currency = d.currency and l.line_type = 'sale') as sale_lines,
           count(*) filter (where l.currency = d.currency and l.line_type = 'sale'
                            and l.gross_minor = d.captured_minor) as sale_lines_at_full_amount,
           coalesce(sum(l.fee_minor) filter (where l.currency = d.currency and l.line_type = 'sale'), 0)
               as settled_fee_minor,
           count(*) filter (where l.currency <> d.currency) as foreign_currency_lines
    from core.settlement_line l
    where l.payment_reference = d.payment_id
) s;

-- Lines that match no payment due in their currency. A line whose payment is due in another currency is a
-- currency mismatch at once; otherwise it awaits for its grace day (the payment may still be held, or its merchant
-- not yet known: P2, P5), then becomes an unexpected settlement (P7).
insert into recon.unmatched_line
select run.as_of_arrival, l.settlement_line_id, l.payment_reference, l.settlement_date, l.line_type, l.currency,
       l.gross_minor,
       case
           when exists (select 1 from due d where d.payment_id = l.payment_reference) then 'currency_mismatch'
           when l.settlement_date >= run.processed_through then 'awaiting'
           else 'unexpected_settlement'
       end
from core.settlement_line l
cross join run
where not exists (select 1 from due d where d.payment_id = l.payment_reference and d.currency = l.currency);

insert into recon.exception
select ps.as_of_arrival, ps.status || ':' || ps.payment_id, ps.status, ps.payment_id, null, ps.currency, ps.due_date,
       ps.expected_minor - ps.settled_gross_minor,
       case when ps.status = 'fee_mismatch' then ps.settled_fee_minor - ps.expected_fee_minor else 0 end,
       greatest(0, run.processed_through - (ps.last_due_date + 1))
from recon.payment_settlement ps
join run using (as_of_arrival)
where ps.status not in ('matched', 'pending')
union all
select u.as_of_arrival, u.status || ':' || u.settlement_line_id, u.status, null, u.settlement_line_id, u.currency,
       u.settlement_date, -u.gross_minor, 0, greatest(0, run.processed_through - (u.settlement_date + 1))
from recon.unmatched_line u
join run using (as_of_arrival)
where u.status <> 'awaiting';

-- Control totals. The left side comes straight from core, not from the classification above, so a payment or
-- line that the classification lost or counted twice shows up as an unexplained difference. Lines are bucketed
-- with their payment's due date when they match it, else under their own settlement date.
insert into recon.control_total (as_of_arrival, currency, due_date, captured_minor, refunded_minor, reversed_minor,
                                 settled_gross_minor, pending_minor, awaiting_minor, open_exception_minor)
select (select as_of_arrival from run), currency, due_date,
       sum(captured), sum(refunded), sum(reversed), sum(settled), sum(pending), sum(awaiting), sum(exceptions)
from (
    select currency, captured_at::date as due_date, captured_minor as captured, refunded_minor as refunded,
           reversed_minor as reversed, 0 as settled, 0 as pending, 0 as awaiting, 0 as exceptions
    from core.payment
    where captured_at is not null
    union all
    select l.currency, coalesce(p.captured_at::date, l.settlement_date), 0, 0, 0, l.gross_minor, 0, 0, 0
    from core.settlement_line l
    left join core.payment p
        on p.payment_id = l.payment_reference and p.currency = l.currency and p.captured_at is not null
    union all
    select currency, due_date, 0, 0, 0, 0, expected_minor - settled_gross_minor, 0, 0
    from recon.payment_settlement
    where as_of_arrival = (select as_of_arrival from run) and status = 'pending'
    union all
    select currency, settlement_date, 0, 0, 0, 0, 0, -gross_minor, 0
    from recon.unmatched_line
    where as_of_arrival = (select as_of_arrival from run) and status = 'awaiting'
    union all
    select currency, due_date, 0, 0, 0, 0, 0, 0, gross_difference_minor
    from recon.exception
    where as_of_arrival = (select as_of_arrival from run)
) parts
group by currency, due_date;
