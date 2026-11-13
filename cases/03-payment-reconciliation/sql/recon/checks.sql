-- Invariant checks on one run, in the same transaction as the run (payrecon.recon.reconcile). Each row returned
-- is a violation; any violation fails the run and rolls it back, so a wrong result is never published.
with run as (
    select current_setting('payrecon.as_of_arrival')::date as as_of_arrival,
           current_setting('payrecon.as_of_arrival')::date - 1 as processed_through
)
-- Invariant 3: every difference is explained by pending, awaiting or an open exception.
select 'invariant 3: unexplained difference' as check_name,
       c.currency || ' ' || c.due_date || ': ' || c.unexplained_minor as detail
from recon.control_total c
join run using (as_of_arrival)
where c.unexplained_minor <> 0
union all
-- Invariant 6: nothing is missing before its grace period ends.
select 'invariant 6: missing within grace', ps.payment_id
from recon.payment_settlement ps
join run using (as_of_arrival)
where ps.status = 'missing_settlement' and ps.last_due_date >= run.processed_through
union all
select 'invariant 6: unexpected within grace', u.settlement_line_id
from recon.unmatched_line u
join run using (as_of_arrival)
where u.status = 'unexpected_settlement' and u.settlement_date >= run.processed_through
union all
-- Completeness: every payment due is classified once.
select 'every payment due is classified', (select count(*) from core.payment where captured_at is not null)
       || ' due, ' || count(*) || ' classified'
from recon.payment_settlement ps
join run using (as_of_arrival)
having count(*) <> (select count(*) from core.payment where captured_at is not null)
union all
-- Invariant 4 and completeness: every settlement line counts towards exactly one payment, or is unmatched.
select 'every line is accounted for once',
       (select count(*) from core.settlement_line) || ' lines, '
       || (select coalesce(sum(settlement_lines), 0) from recon.payment_settlement ps join run using (as_of_arrival))
       || ' matched, '
       || (select count(*) from recon.unmatched_line u join run using (as_of_arrival)) || ' unmatched'
where (select count(*) from core.settlement_line)
      <> (select coalesce(sum(settlement_lines), 0) from recon.payment_settlement ps join run using (as_of_arrival))
         + (select count(*) from recon.unmatched_line u join run using (as_of_arrival));
