-- Rebuild core from raw as of one arrival date: only files that had arrived by then are read. The caller sets
-- payrecon.as_of_arrival for the transaction and runs this whole file in that transaction (payrecon.core.build).
-- Rebuilding rather than updating in place makes a rerun of any day give identical results (invariant 8).

truncate core.illegal_transition, core.payment, core.payment_event, core.settlement_line, core.merchant;

create temporary table arrived on commit drop as
select file_id
from ops.file
where arrival_date <= current_setting('payrecon.as_of_arrival')::date;

-- Merchants: the latest version delivered (by updated_at, then by delivery).
insert into core.merchant
select distinct on (m.merchant_id)
    m.merchant_id, m.merchant_name, m.country, m.currency, m.fee_bps, m.fee_fixed_minor, m.updated_at, m.file_id
from raw.merchant m
join arrived using (file_id)
order by m.merchant_id, m.updated_at desc, m.file_id desc, m.row_number desc;

-- Events whose merchant is not yet in reference data cannot be applied: they wait in quarantine (stage 'core') and
-- are read again on every build, so they apply as soon as the merchant arrives (planted P5).
insert into ops.quarantine (file_id, row_number, stage, reason, raw_row)
select e.file_id, e.row_number, 'core', 'merchant ' || e.merchant_id || ' is not in reference data',
       to_jsonb(e) - 'file_id' - 'row_number'
from raw.payment_event e
join arrived using (file_id)
where not exists (select 1 from core.merchant m where m.merchant_id = e.merchant_id)
on conflict (file_id, row_number, stage) do nothing;

update ops.quarantine q
set reprocessed_at = now()
from raw.payment_event e
where q.stage = 'core'
  and q.reprocessed_at is null
  and e.file_id = q.file_id
  and e.row_number = q.row_number
  and exists (select 1 from core.merchant m where m.merchant_id = e.merchant_id);

-- Distinct events: the first delivery of each event_id is kept; later deliveries are only counted (planted P1).
insert into core.payment_event
select event_id, payment_id, merchant_id, event_type, sequence_no, event_time, ingestion_time, amount_minor,
       currency, file_id, row_number, delivery_count
from (
    select e.*,
           row_number() over (partition by e.event_id order by e.file_id, e.row_number) as delivery,
           count(*) over (partition by e.event_id) as delivery_count
    from raw.payment_event e
    join arrived using (file_id)
    where exists (select 1 from core.merchant m where m.merchant_id = e.merchant_id)
) first_delivery
where delivery = 1;

-- Fresh statistics after the reload, or the planner treats these tables as empty and the walk below crawls.
analyze core.merchant;
analyze core.payment_event;

-- Apply each payment's events in sequence order, never in arrival order (planted P2). The walk starts from a
-- 'new' state at sequence 0 and steps to sequence + 1, so it stops at the first gap: later events are held.
-- An event with no allowed transition from the current state, or a refund larger than what remains captured,
-- is not applied; the state carries over to the next event (planted P3).
create temporary table walk on commit drop as
with recursive step as (
    select p.payment_id, 0 as sequence_no, null::text as event_id, null::text as event_type, 'new'::text as status,
           true as applied, null::text as reason, null::timestamp as event_time,
           null::timestamp as initiated_at, null::timestamp as captured_at, null::timestamp as status_changed_at,
           0::bigint as captured_minor, 0::bigint as refunded_minor, 0::bigint as reversed_minor
    from (select distinct payment_id from core.payment_event) p
    union all
    select s.payment_id, e.sequence_no, e.event_id, e.event_type,
           case when x.applied then t.to_status else s.status end,
           x.applied,
           case when t.to_status is null then 'no transition from ' || s.status || ' on ' || e.event_type
                when not x.applied then 'refund of ' || e.amount_minor || ' exceeds the '
                                        || (s.captured_minor - s.refunded_minor) || ' remaining' end,
           e.event_time,
           case when x.applied and e.event_type = 'initiated' then e.event_time else s.initiated_at end,
           case when x.applied and e.event_type = 'captured' then e.event_time else s.captured_at end,
           case when x.applied then e.event_time else s.status_changed_at end,
           case when x.applied and e.event_type = 'captured' then e.amount_minor else s.captured_minor end,
           s.refunded_minor + case when x.applied and e.event_type = 'refunded' then e.amount_minor else 0 end,
           case when x.applied and e.event_type = 'reversed' then s.captured_minor - s.refunded_minor
                else s.reversed_minor end
    from step s
    join core.payment_event e on e.payment_id = s.payment_id and e.sequence_no = s.sequence_no + 1
    left join core.allowed_transition t on t.from_status = s.status and t.event_type = e.event_type
    cross join lateral (
        select t.to_status is not null
               and (e.event_type <> 'refunded' or e.amount_minor <= s.captured_minor - s.refunded_minor) as applied
    ) x
)
select * from step;

analyze walk;

insert into core.illegal_transition (event_id, payment_id, sequence_no, from_status, event_type, reason)
select w.event_id, w.payment_id, w.sequence_no, previous.status, w.event_type, w.reason
from walk w
join walk previous on previous.payment_id = w.payment_id and previous.sequence_no = w.sequence_no - 1
where not w.applied;

-- Payment: the last step of its walk, with merchant and currency from its first event received.
insert into core.payment (payment_id, merchant_id, currency, status, last_sequence_no, held_events, initiated_at,
                          captured_at, status_changed_at, captured_minor, refunded_minor, reversed_minor)
select w.payment_id, f.merchant_id, f.currency, w.status, w.sequence_no,
       (select count(*) from core.payment_event e where e.payment_id = w.payment_id and e.sequence_no > w.sequence_no),
       w.initiated_at, w.captured_at, w.status_changed_at, w.captured_minor, w.refunded_minor, w.reversed_minor
from (select distinct on (payment_id) * from walk order by payment_id, sequence_no desc) w
join (select distinct on (payment_id) payment_id, merchant_id, currency
      from core.payment_event order by payment_id, sequence_no) f using (payment_id);

-- Settlement lines: the latest delivery of each line id wins, so a corrected re-issue supersedes the original.
insert into core.settlement_line
select settlement_line_id, settlement_date, payment_reference, line_type, currency, gross_minor, fee_minor,
       net_minor, file_id, row_number, delivery_count
from (
    select l.*,
           row_number() over (partition by l.settlement_line_id order by l.file_id desc, l.row_number desc) as recency,
           count(*) over (partition by l.settlement_line_id) as delivery_count
    from raw.settlement_line l
    join arrived using (file_id)
) latest
where recency = 1;

insert into core.build (as_of_arrival, built_at)
values (current_setting('payrecon.as_of_arrival')::date, now())
on conflict (singleton) do update set as_of_arrival = excluded.as_of_arrival, built_at = excluded.built_at;
