-- Caller supplies affected(payment_id) and transaction-local payrecon.live_generation.
create temporary table projection_event on commit drop as
select e.event_id,e.payment_id,e.sequence_no,e.event->>'merchant_id' as merchant_id,
       e.event->>'currency' as currency,e.event->>'event_type' as event_type,
       (e.event->>'event_time')::timestamptz at time zone 'UTC' as event_time,
       (e.event->>'amount_minor')::bigint as amount_minor
from stream.live_event e join affected using(payment_id)
where e.generation=current_setting('payrecon.live_generation')::uuid;

create temporary table bad_identity on commit drop as
select payment_id from projection_event e left join stream.reference_merchant m using(merchant_id)
group by payment_id
having count(distinct (e.merchant_id,e.currency)) > 1
    or bool_or(m.currency is not null and m.currency <> e.currency);

create temporary table walk_event on commit drop as
select e.* from projection_event e join stream.reference_merchant m using(merchant_id)
where not exists(select 1 from bad_identity b where b.payment_id=e.payment_id);
create index on walk_event(payment_id,sequence_no);
analyze walk_event;
create temporary table walk_transition on commit drop as table stream.allowed_transition;
-- include: ../shared/payment_walk.sql

delete from stream.event_application a using affected p
where a.generation=current_setting('payrecon.live_generation')::uuid and a.payment_id=p.payment_id;
insert into stream.event_application(generation,event_id,payment_id,outcome,reason)
select current_setting('payrecon.live_generation')::uuid,e.event_id,e.payment_id,
       case when b.payment_id is not null then 'identity_mismatch'
            when m.merchant_id is null then 'unknown_reference'
            when w.event_id is null then 'held'
            when w.applied then 'applied' else 'illegal' end,
       case when b.payment_id is not null then 'payment merchant/currency identity disagrees'
            when m.merchant_id is null then 'merchant is absent at the reference frontier'
            when w.event_id is null then 'missing earlier sequence' else w.reason end
from projection_event e
left join bad_identity b using(payment_id)
left join stream.reference_merchant m using(merchant_id)
left join walk w using(event_id);

delete from stream.payment_latest p using affected a
where p.generation=current_setting('payrecon.live_generation')::uuid and p.payment_id=a.payment_id;
insert into stream.payment_latest(generation,payment_id,merchant_id,currency,status,last_sequence_no,
    held_events,initiated_at,captured_at,status_changed_at,captured_minor,refunded_minor,reversed_minor)
select current_setting('payrecon.live_generation')::uuid,w.payment_id,f.merchant_id,f.currency,w.status,
    w.sequence_no,(select count(*) from walk_event e where e.payment_id=w.payment_id
        and e.sequence_no>w.sequence_no),w.initiated_at,w.captured_at,w.status_changed_at,
    w.captured_minor,w.refunded_minor,w.reversed_minor
from (select distinct on(payment_id) * from walk order by payment_id,sequence_no desc) w
join (select distinct on(payment_id) payment_id,merchant_id,currency
      from walk_event order by payment_id,sequence_no) f using(payment_id);

-- A newly filled sequence or reference can revise old minutes. Replace every bucket touched by these
-- complete payment histories, aggregating all payments in each bucket to avoid losing their neighbours.
create temporary table affected_bucket on commit drop as
select distinct date_trunc('minute',event_time) at time zone 'UTC' as minute_start,merchant_id,currency
from projection_event;
delete from stream.event_minute m using affected_bucket b
where m.generation=current_setting('payrecon.live_generation')::uuid
  and (m.minute_start,m.merchant_id,m.currency)=(b.minute_start,b.merchant_id,b.currency);
insert into stream.event_minute(generation,minute_start,merchant_id,currency,event_type,outcome,event_count)
select e.generation,date_trunc('minute',(e.event->>'event_time')::timestamptz),
       e.event->>'merchant_id',e.event->>'currency',e.event->>'event_type',a.outcome,count(*)
from stream.live_event e join stream.event_application a using(generation,event_id)
join affected_bucket b on b.minute_start=date_trunc('minute',(e.event->>'event_time')::timestamptz)
    and b.merchant_id=e.event->>'merchant_id' and b.currency=e.event->>'currency'
where e.generation=current_setting('payrecon.live_generation')::uuid
group by 1,2,3,4,5,6;
