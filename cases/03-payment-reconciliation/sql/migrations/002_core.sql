-- Core: cleaned and derived tables, rebuilt from raw as of an arrival date by sql/core/build.sql (decision 0004).
-- No foreign keys between core tables or back to ops.file: core is derived whole from validated raw, and per-row
-- constraint triggers cost about 11 seconds of a full build. Integrity is asserted by tests instead (decision 0004).

create schema if not exists core;

-- The payment state machine as data, so the build and the docs cannot drift apart (docs/data-model.md).
-- 'new' is the state before any event has been applied.
create table core.allowed_transition (
    from_status text not null,
    event_type text not null,
    to_status text not null,
    primary key (from_status, event_type)
);

insert into core.allowed_transition (from_status, event_type, to_status)
-- include: ../shared/allowed_transitions.sql

-- Which arrival the core tables currently reflect. One row.
create table core.build (
    singleton boolean primary key default true check (singleton),
    as_of_arrival date not null,
    built_at timestamptz not null
);

-- Merchant, current as of the build: the latest version delivered.
create table core.merchant (
    merchant_id text primary key,
    merchant_name text not null,
    country text not null,
    currency char(3) not null,
    fee_bps integer not null,
    fee_fixed_minor bigint not null,
    updated_at timestamp not null,
    file_id bigint not null
);

-- Distinct event: the first delivery of each event_id. A sequence number identifies one event within its payment;
-- two different events claiming the same position break that contract and stop the build rather than be guessed.
create table core.payment_event (
    event_id text primary key,
    payment_id text not null,
    merchant_id text not null,
    event_type text not null,
    sequence_no integer not null,
    event_time timestamp not null,
    ingestion_time timestamp not null,
    amount_minor bigint not null,
    currency char(3) not null,
    file_id bigint not null,
    row_number integer not null,
    delivery_count integer not null,
    unique (payment_id, sequence_no)
);

-- Payment, current state: its events applied in sequence order through the allowed transitions only. Events past a
-- gap in the sequence are held, not applied, until the missing event arrives.
create table core.payment (
    payment_id text primary key,
    merchant_id text not null,
    currency char(3) not null,
    status text not null,
    last_sequence_no integer not null,   -- the last event processed in the unbroken sequence from 1
    held_events integer not null,        -- events received beyond a gap in the sequence
    initiated_at timestamp,
    captured_at timestamp,
    status_changed_at timestamp,
    captured_minor bigint not null,
    refunded_minor bigint not null,
    reversed_minor bigint not null,
    -- Captured minus refunds; a reversal takes it to zero (settlement rules, requirements.md).
    expected_settlement_minor bigint not null generated always as (captured_minor - refunded_minor - reversed_minor) stored,
    check (expected_settlement_minor >= 0)
);

-- Event that was processed in sequence but could not be applied.
create table core.illegal_transition (
    event_id text primary key,
    payment_id text not null,
    sequence_no integer not null,
    from_status text not null,
    event_type text not null,
    reason text not null
);

-- Distinct settlement line: the latest delivery of each line id, so a corrected re-issue supersedes the original.
-- An identical re-send never reaches raw (the loader skips it by checksum).
create table core.settlement_line (
    settlement_line_id text primary key,
    settlement_date date not null,
    payment_reference text not null,
    line_type text not null,
    currency char(3) not null,
    gross_minor bigint not null,
    fee_minor bigint not null,
    net_minor bigint not null,
    file_id bigint not null,
    row_number integer not null,
    delivery_count integer not null
);

create index on core.settlement_line (payment_reference);

-- A raw row is quarantined at most once per stage, so rebuilding core never repeats an entry.
create unique index quarantine_once_per_stage on ops.quarantine (file_id, row_number, stage);
