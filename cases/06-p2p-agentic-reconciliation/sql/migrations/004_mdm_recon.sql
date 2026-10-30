-- Supplier master data and reconciliation results.

-- Grain: one real-world supplier. Rebuilt on every resolution run; the id derives from the lowest member key,
-- so it only changes if a lower-keyed record joins the cluster.
create table mdm.supplier_golden (
    supplier_gid   text primary key,
    golden_name    text not null,
    vat_number     text,
    country_code   char(2),
    member_count   integer not null,
    run_id         bigint not null references ops.pipeline_run
);

-- Grain: one source supplier. Source-to-golden mapping; every core.supplier has exactly one row.
create table mdm.supplier_xref (
    source_system       text not null,
    source_supplier_id  text not null,
    supplier_gid        text not null references mdm.supplier_golden,
    match_method        text not null check (match_method in
                          ('SINGLETON', 'EXACT_VAT', 'EXACT_BANK', 'FUZZY', 'STEWARD')),
    match_score         numeric(5,2),
    run_id              bigint not null references ops.pipeline_run,
    primary key (source_system, source_supplier_id),
    foreign key (source_system, source_supplier_id) references core.supplier
);

-- Grain: one unordered pair of source suppliers worth recording (merged, or sent to a steward). a sorts before b.
create table mdm.supplier_match_candidate (
    a_source_system       text not null,
    a_source_supplier_id  text not null,
    b_source_system       text not null,
    b_source_supplier_id  text not null,
    method                text not null,
    name_score            numeric(5,2) not null,
    address_score         numeric(5,2) not null,
    combined_score        numeric(5,2) not null,
    decision              text not null check (decision in ('AUTO_MERGE', 'STEWARD_REVIEW', 'STEWARD_MERGE', 'STEWARD_REJECT')),
    evidence              text not null,
    run_id                bigint not null references ops.pipeline_run,
    primary key (a_source_system, a_source_supplier_id, b_source_system, b_source_supplier_id)
);

-- Grain: one steward decision on one pair; the latest wins. Append-only, and it survives every rebuild above.
create table mdm.steward_decision (
    decision_id           bigint generated always as identity primary key,
    a_source_system       text not null,
    a_source_supplier_id  text not null,
    b_source_system       text not null,
    b_source_supplier_id  text not null,
    decision              text not null check (decision in ('MERGE', 'REJECT')),
    decided_by            text not null,
    decided_at            timestamptz not null default now()
);

-- Grain: one execution of the matching engine. Result tables below are snapshots per run, so that two runs
-- can be compared line by line and a period can be re-examined. Consumers read the recon.current_* views.
create table recon.match_run (
    run_id        bigint primary key references ops.pipeline_run,
    cutoff_date   date not null,
    created_at    timestamptz not null default now()
);

-- Grain: one requisition x control reason x run.
create table recon.control_breach (
    run_id         bigint not null references recon.match_run,
    source_system  text not null,
    pr_number      text not null,
    company_code   text not null,
    reason_code    text not null,
    evidence       jsonb not null,
    primary key (run_id, source_system, pr_number, reason_code)
);

-- Grain: one invoice line x run.
create table recon.match_result (
    run_id             bigint not null references recon.match_run,
    source_system      text not null,
    invoice_id         text not null,
    line_number        integer not null,
    company_code       text not null,
    currency_code      char(3) not null,
    document_type      text not null,
    match_type         text not null check (match_type in ('THREE_WAY', 'TWO_WAY', 'CREDIT', 'NONE')),
    status_code        text not null check (status_code in ('Matched', 'MatchedWithinTolerance', 'Exception', 'PendingReceipt')),
    net_amount         numeric(18,2) not null,
    expected_amount    numeric(18,2),
    amount_variance    numeric(18,2),
    tolerance_pct      numeric(7,4),
    tolerance_source   text,
    cumulative_invoiced numeric(18,3),
    allowed_quantity   numeric(18,3),
    primary key (run_id, source_system, invoice_id, line_number)
);

-- Grain: one invoice line x reason x run. Reason codes are SKOS concepts in ontology/vocab.ttl.
create table recon.exception (
    run_id         bigint not null,
    source_system  text not null,
    invoice_id     text not null,
    line_number    integer not null,
    reason_code    text not null,
    evidence       jsonb not null,
    primary key (run_id, source_system, invoice_id, line_number, reason_code),
    foreign key (run_id, source_system, invoice_id, line_number) references recon.match_result
);

-- Grain: one goods PO line with a positive received-not-invoiced quantity x cut-off x run.
-- PO line rather than GRN line: invoices reference PO lines, so allocating them to GRN lines would be invented.
create table recon.grni_line (
    run_id             bigint not null references recon.match_run,
    cutoff_date        date not null,
    source_system      text not null,
    po_number          text not null,
    line_number        integer not null,
    company_code       text not null,
    currency_code      char(3) not null,
    gl_account_code    text not null,
    received_quantity  numeric(18,3) not null,
    invoiced_quantity  numeric(18,3) not null,
    grni_quantity      numeric(18,3) not null check (grni_quantity > 0),
    unit_price         numeric(18,6) not null,
    grni_amount        numeric(18,2) not null,
    primary key (run_id, source_system, po_number, line_number)
);

-- Grain: one company x currency x account x side x cut-off x run. Proposed journal only; nothing is posted anywhere.
create table recon.accrual_journal_line (
    run_id           bigint not null references recon.match_run,
    cutoff_date      date not null,
    company_code     text not null,
    currency_code    char(3) not null,
    gl_account_code  text not null,
    side             char(1) not null check (side in ('D', 'C')),
    amount           numeric(18,2) not null check (amount > 0),
    primary key (run_id, company_code, currency_code, gl_account_code, side)
);
