-- Reconciliation results, kept per run (decision 0005). A run is identified by its arrival date: rerunning a day
-- replaces that day's results, so a day's results depend only on what had arrived by then (invariant 8), while
-- ops.run keeps a log of every execution.

create schema if not exists recon;

create table ops.run (
    run_id bigserial primary key,
    as_of_arrival date not null,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    status text not null default 'running' check (status in ('running', 'succeeded', 'failed')),
    detail jsonb    -- row counts when it succeeded, the error when it failed
);

-- Payment due for settlement (captured at some point), per run, with how it was classified.
create table recon.payment_settlement (
    as_of_arrival date not null,
    payment_id text not null,
    merchant_id text not null,
    currency char(3) not null,
    due_date date not null,                 -- capture date: the sale is due in that day's settlement file
    last_due_date date not null,            -- the latest refund or reversal, if any, else the capture date
    captured_minor bigint not null,
    refunded_minor bigint not null,
    reversed_minor bigint not null,
    expected_minor bigint not null,         -- captured - refunded - reversed
    settled_gross_minor bigint not null,    -- lines in the payment's own currency only
    settlement_lines integer not null,
    sale_lines_at_full_amount integer not null,
    expected_fee_minor bigint not null,
    settled_fee_minor bigint not null,
    foreign_currency_lines integer not null,
    status text not null check (status in ('matched', 'pending', 'missing_settlement', 'amount_mismatch',
                                           'duplicate_settlement', 'currency_mismatch', 'fee_mismatch')),
    primary key (as_of_arrival, payment_id)
);

-- Settlement line that matches no payment due in its currency, per run.
create table recon.unmatched_line (
    as_of_arrival date not null,
    settlement_line_id text not null,
    payment_reference text not null,
    settlement_date date not null,
    line_type text not null,
    currency char(3) not null,
    gross_minor bigint not null,
    status text not null check (status in ('awaiting', 'unexpected_settlement', 'currency_mismatch')),
    primary key (as_of_arrival, settlement_line_id)
);

-- Open exception, per run: the work list. One row per payment or line and exception type. The signed gross
-- difference (expected - settled) is what control totals need to be explained; fee differences are separate.
create table recon.exception (
    as_of_arrival date not null,
    exception_id text not null,
    exception_type text not null,
    payment_id text,
    settlement_line_id text,
    currency char(3) not null,
    due_date date not null,
    gross_difference_minor bigint not null,
    fee_difference_minor bigint not null,
    age_days integer not null,              -- days since the grace period ended
    primary key (as_of_arrival, exception_id),
    check ((payment_id is null) <> (settlement_line_id is null))
);

-- Currency x due date, per run. The left side (captured - refunded - reversed - settled) is computed from core,
-- independently of the classification; the right side from it. unexplained_minor must be 0 (invariant 3).
create table recon.control_total (
    as_of_arrival date not null,
    currency char(3) not null,
    due_date date not null,
    captured_minor bigint not null,
    refunded_minor bigint not null,
    reversed_minor bigint not null,
    settled_gross_minor bigint not null,
    pending_minor bigint not null,
    awaiting_minor bigint not null,
    open_exception_minor bigint not null,
    unexplained_minor bigint not null generated always as (
        captured_minor - refunded_minor - reversed_minor - settled_gross_minor
        - pending_minor - awaiting_minor - open_exception_minor) stored,
    primary key (as_of_arrival, currency, due_date)
);
