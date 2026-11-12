-- Separate stream database, applied only through stream-prepare. No financial core/recon writes.
create schema if not exists stream;
create schema if not exists ops;

create table stream.dataset (
    singleton boolean primary key default true check (singleton),
    dataset_id text not null unique,
    source_start timestamptz not null,
    source_end timestamptz not null check (source_end > source_start)
);
create table stream.lineage (
    generation uuid primary key,
    dataset_id text not null references stream.dataset(dataset_id),
    cluster_id text not null,
    topic_id text not null,
    topic text not null,
    checkpoint_path text not null unique,
    created_at timestamptz not null default now()
);
create table stream.delivery (
    cluster_id text not null,
    topic_id text not null,
    partition integer not null,
    "offset" bigint not null,
    topic text not null,
    key bytea,
    value bytea,
    broker_time timestamptz,
    first_intake_at timestamptz not null default now(),
    primary key (cluster_id, topic_id, partition, "offset")
);
create table stream.intake_event (
    dataset_id text not null references stream.dataset(dataset_id),
    event_id text not null,
    payment_id text not null,
    sequence_no bigint not null,
    semantic_hash text not null,
    event jsonb not null,
    first_intake_at timestamptz not null default now(),
    primary key (dataset_id, event_id),
    unique (dataset_id, payment_id, sequence_no)
);
create table stream.quarantine (
    cluster_id text not null,
    topic_id text not null,
    partition integer not null,
    "offset" bigint not null,
    reason text not null,
    detail text not null,
    blocking boolean not null,
    primary key (cluster_id, topic_id, partition, "offset", reason),
    foreign key (cluster_id, topic_id, partition, "offset")
        references stream.delivery
);
create table ops.stream_batch (
    generation uuid not null references stream.lineage,
    query_role text not null check (query_role = 'intake'),
    batch_id bigint not null,
    input_hash text not null,
    summary jsonb not null,
    committed_at timestamptz not null default now(),
    primary key (generation, query_role, batch_id)
);
