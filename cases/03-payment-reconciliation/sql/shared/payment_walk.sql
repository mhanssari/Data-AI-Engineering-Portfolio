-- Shared sequence walk: caller supplies walk_event and walk_transition. All timestamps are UTC.
create temporary table walk on commit drop as
with recursive step as (
    select p.payment_id, 0 as sequence_no, null::text as event_id, null::text as event_type, 'new'::text as status,
           true as applied, null::text as reason, null::timestamp as event_time,
           null::timestamp as initiated_at, null::timestamp as captured_at, null::timestamp as status_changed_at,
           0::bigint as captured_minor, 0::bigint as refunded_minor, 0::bigint as reversed_minor
    from (select distinct payment_id from walk_event) p
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
    join walk_event e on e.payment_id = s.payment_id and e.sequence_no = s.sequence_no + 1
    left join walk_transition t on t.from_status = s.status and t.event_type = e.event_type
    cross join lateral (
        select t.to_status is not null
               and (e.event_type <> 'refunded' or e.amount_minor <= s.captured_minor - s.refunded_minor) as applied
    ) x
)
select * from step;

analyze walk;
