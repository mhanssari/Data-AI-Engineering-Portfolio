-- Only completed repairs are committed here. A failed repair rolls back all effects; its error is in the caller log.
create table ops.stream_backfill (
    repair_id text primary key,
    generation uuid not null references stream.lineage,
    request_hash text not null,
    plan jsonb not null,
    reason text not null check (length(trim(reason)) between 1 and 1000),
    status text not null check (status='completed'),
    summary jsonb not null,
    completed_at timestamptz not null default now()
);
