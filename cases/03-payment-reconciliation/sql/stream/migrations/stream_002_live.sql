-- Separate checkpoint roles; existing intake markers acquire their original role without a reset.
alter table stream.lineage add column query_role text not null default 'intake'
    check (query_role in ('intake','live'));
alter table ops.stream_batch drop constraint stream_batch_query_role_check;
alter table ops.stream_batch add check (query_role in ('intake','live'));

create table stream.allowed_transition (
    from_status text not null,
    event_type text not null,
    to_status text not null,
    primary key(from_status,event_type)
);
insert into stream.allowed_transition(from_status,event_type,to_status)
-- include: ../../shared/allowed_transitions.sql

-- Reference snapshot is case-owned, independently seeded and fenced with the live writer.
create table stream.reference_snapshot (
    singleton boolean primary key default true check (singleton),
    seed integer not null,
    as_of_arrival date not null,
    content_hash text not null,
    loaded_at timestamptz not null default now()
);
create table stream.reference_merchant (
    merchant_id text primary key,
    currency text not null
);
create table stream.live_event (
    generation uuid not null references stream.lineage,
    event_id text not null,
    payment_id text not null,
    sequence_no integer not null,
    semantic_hash text not null,
    event jsonb not null,
    admission_origin text not null default 'live' check (admission_origin in ('live','backfill')),
    first_commit_at timestamptz not null default now(),
    primary key(generation,event_id),
    unique(generation,payment_id,sequence_no)
);
create table stream.live_conflict (
    generation uuid not null references stream.lineage,
    batch_id bigint not null,
    event_id text not null,
    semantic_hash text not null,
    event jsonb not null,
    reason text not null,
    primary key(generation,batch_id,event_id,semantic_hash)
);
create table stream.event_application (
    generation uuid not null,
    event_id text not null,
    payment_id text not null,
    outcome text not null check (outcome in ('applied','held','illegal','unknown_reference','identity_mismatch')),
    reason text,
    primary key(generation,event_id),
    foreign key(generation,event_id) references stream.live_event
);
create table stream.payment_latest (
    generation uuid not null references stream.lineage,
    payment_id text not null,
    merchant_id text not null,
    currency text not null,
    status text not null,
    last_sequence_no integer not null,
    held_events integer not null,
    initiated_at timestamp,
    captured_at timestamp,
    status_changed_at timestamp,
    captured_minor bigint not null,
    refunded_minor bigint not null,
    reversed_minor bigint not null,
    expected_settlement_minor bigint not null generated always as
        (captured_minor-refunded_minor-reversed_minor) stored check (expected_settlement_minor >= 0),
    primary key(generation,payment_id)
);
create table stream.event_minute (
    generation uuid not null references stream.lineage,
    minute_start timestamptz not null,
    merchant_id text not null,
    currency text not null,
    event_type text not null,
    outcome text not null,
    event_count bigint not null check(event_count > 0),
    primary key(generation,minute_start,merchant_id,currency,event_type,outcome)
);
create view stream.operation_minute as
select generation,minute_start,merchant_id,currency,
    coalesce(sum(event_count) filter(where outcome='applied' and event_type='authorised'),0) as authorised,
    coalesce(sum(event_count) filter(where outcome='applied' and event_type='declined'),0) as declined,
    coalesce(sum(event_count) filter(where outcome='applied' and event_type='expired'),0) as expired,
    100.0 * coalesce(sum(event_count) filter(where outcome='applied' and event_type='declined'),0)
      / nullif(sum(event_count) filter(where outcome='applied' and event_type in ('authorised','declined')),0)
      as decline_rate_pct,
    coalesce(sum(event_count) filter(where outcome='held'),0) as held,
    coalesce(sum(event_count) filter(where outcome='illegal'),0) as illegal,
    coalesce(sum(event_count) filter(where outcome='unknown_reference'),0) as unknown_reference,
    coalesce(sum(event_count) filter(where outcome='identity_mismatch'),0) as identity_mismatch
from stream.event_minute group by generation,minute_start,merchant_id,currency;

-- Progress is independently reported after sink commit; it is never a cross-query transaction.
create table ops.stream_progress (
    generation uuid not null references stream.lineage,
    query_role text not null check(query_role in ('intake','live')),
    batch_id bigint not null,
    progress jsonb not null,
    reported_at timestamptz not null default now(),
    primary key(generation,query_role,batch_id)
);
