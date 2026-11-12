-- Landing (raw) and operations (ops). Raw is append-only: one row per delivered row that passed validation, with
-- the file and row it came from. Rows that fail validation go to ops.quarantine instead, with their reason.

create schema if not exists raw;
create schema if not exists ops;

-- One row per arrived file. A file whose name and checksum are already here is skipped: a re-sent file lands nothing.
create table ops.file (
    file_id bigserial primary key,
    arrival_date date not null,
    file_name text not null,
    kind text not null check (kind in ('merchants', 'events', 'settlement')),
    sha256 text not null,
    row_count integer not null,
    loaded_rows integer not null,
    quarantined_rows integer not null,
    loaded_at timestamptz not null default now(),
    unique (file_name, sha256)
);

-- One row per rejected record: where it came from, what it said, and why. Stage 'load' is a malformed row;
-- stage 'core' is a well-formed row that cannot be applied yet, for example an unknown merchant.
create table ops.quarantine (
    quarantine_id bigserial primary key,
    file_id bigint not null references ops.file,
    row_number integer not null,
    stage text not null check (stage in ('load', 'core')),
    reason text not null,
    raw_row jsonb not null,
    quarantined_at timestamptz not null default now(),
    reprocessed_at timestamptz
);

create table raw.merchant (
    file_id bigint not null references ops.file,
    row_number integer not null,
    merchant_id text not null,
    merchant_name text not null,
    country text not null,
    currency char(3) not null,
    fee_bps integer not null,
    fee_fixed_minor bigint not null,
    updated_at timestamp not null,
    primary key (file_id, row_number)
);

create table raw.payment_event (
    file_id bigint not null references ops.file,
    row_number integer not null,
    event_id text not null,
    payment_id text not null,
    merchant_id text not null,
    event_type text not null,
    sequence_no integer not null,
    event_time timestamp not null,
    ingestion_time timestamp not null,
    amount_minor bigint not null,
    currency char(3) not null,
    primary key (file_id, row_number)
);

create table raw.settlement_line (
    file_id bigint not null references ops.file,
    row_number integer not null,
    settlement_line_id text not null,
    settlement_date date not null,
    payment_reference text not null,
    line_type text not null,
    currency char(3) not null,
    gross_minor bigint not null,
    fee_minor bigint not null,
    net_minor bigint not null,
    primary key (file_id, row_number)
);
