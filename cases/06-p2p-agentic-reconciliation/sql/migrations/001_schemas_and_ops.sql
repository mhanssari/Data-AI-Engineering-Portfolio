-- Schemas, extensions and operational tables.
-- docs and agent are created now so that the schema layout is fixed; their tables arrive in milestone 2.

create extension if not exists vector;

create schema if not exists raw;    -- source files landed unchanged, append-only
create schema if not exists stg;    -- typed, renamed views over raw; no storage
create schema if not exists core;   -- canonical procure-to-pay model named after ontology classes
create schema if not exists mdm;    -- supplier golden records and cross-reference
create schema if not exists recon;  -- controls, match results, exceptions, GRNI, accrual journal
create schema if not exists docs;   -- document chunks and embeddings (milestone 2)
create schema if not exists agent;  -- agent runs, tool calls, proposals, decisions (milestone 2)
create schema if not exists ops;    -- runs, files, watermarks, quarantine, data quality, lineage

-- Grain: one execution of one CLI step.
create table ops.pipeline_run (
    run_id         bigint generated always as identity primary key,
    step           text not null,
    status         text not null check (status in ('running', 'succeeded', 'failed')),
    parameters     jsonb not null default '{}',
    row_counts     jsonb not null default '{}',
    error_message  text,
    started_at     timestamptz not null default now(),
    finished_at    timestamptz
);

-- Grain: one source file content landed once. The checksum makes a re-delivered file a no-op.
create table ops.ingest_file (
    source_system  text not null,
    file_name      text not null,
    checksum       text not null,
    batch_id       text not null,
    extracted_at   timestamptz not null,
    row_count      integer not null,
    run_id         bigint not null references ops.pipeline_run,
    primary key (source_system, file_name, checksum)
);

-- Grain: one source system x canonical entity. Conform reads raw rows changed after
-- (high_watermark - lookback); the lookback catches late arrivals, and upsert-if-newer
-- makes the overlap harmless.
create table ops.watermark (
    source_system   text not null,
    entity          text not null,
    high_watermark  timestamptz not null default '-infinity',
    lookback        interval not null default interval '24 hours',
    updated_run_id  bigint references ops.pipeline_run,
    primary key (source_system, entity)
);

-- Grain: one raw row that could not enter core because a reference did not resolve.
-- Open entries are retried on every conform run; resolved_run_id records when they cleared.
create table ops.quarantine (
    quarantine_id   bigint generated always as identity primary key,
    entity          text not null,
    source_system   text not null,
    natural_key     text not null,
    raw_id          bigint not null,
    reason          text not null,
    first_run_id    bigint not null references ops.pipeline_run,
    last_run_id     bigint not null references ops.pipeline_run,
    resolved_run_id bigint references ops.pipeline_run,
    unique (entity, raw_id)
);

-- Grain: one data-quality check result for one scope (for example company and currency) in one run.
create table ops.dq_result (
    run_id      bigint not null references ops.pipeline_run,
    check_name  text not null,
    scope       text not null default '',
    expected    numeric,
    actual      numeric,
    status      text not null check (status in ('pass', 'fail', 'warn')),
    detail      text,
    primary key (run_id, check_name, scope)
);

-- Grain: one dataset-to-dataset movement in one run (run-level lineage, not row-level).
-- Row-level lineage lives on every core row as source_raw_id -> raw.<table>.raw_id.
create table ops.lineage_edge (
    run_id          bigint not null references ops.pipeline_run,
    source_dataset  text not null,
    target_dataset  text not null,
    row_count       integer not null,
    primary key (run_id, source_dataset, target_dataset)
);
