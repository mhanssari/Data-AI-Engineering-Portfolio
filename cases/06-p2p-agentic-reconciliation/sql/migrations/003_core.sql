-- Canonical procure-to-pay model. Table names follow ontology classes (ontology/mapping.yaml).
-- Every table carries row-level lineage: source_raw_id -> raw.<table>.raw_id, plus the source change
-- timestamp that upsert-if-newer compares and a soft-delete flag driven by CDC 'D' rows.
-- Foreign keys are real wherever the parent has one row per key. Versioned parents (employee, contract)
-- have no single-row target, so conform checks those references instead.
-- Amounts NUMERIC(18,2), unit prices NUMERIC(18,6), quantities NUMERIC(18,3). No floats anywhere.

-- Grain: one company.
create table core.legal_entity (
    company_code       text primary key,
    entity_name        text not null,
    source_system      text not null,
    source_org_ref     text not null,
    currency_code      char(3) not null,
    country_code       char(2) not null,
    grni_account       text not null,
    source_raw_id      bigint not null,
    source_changed_at  timestamptz not null,
    last_run_id        bigint not null references ops.pipeline_run,
    is_deleted         boolean not null default false
);

-- Grain: one policy parameter in one policy version. Controls read the version in force at the document date.
create table core.policy_parameter (
    parameter_code     text not null,
    policy_version     text not null,
    parameter_value    numeric(18,4) not null,
    unit               text not null,
    valid_from         date not null,
    source_system      text not null,
    source_raw_id      bigint not null,
    source_changed_at  timestamptz not null,
    last_run_id        bigint not null references ops.pipeline_run,
    is_deleted         boolean not null default false,
    primary key (parameter_code, policy_version)
);

-- Grain: one directed currency pair from one effective date. Used for approval limits only; never to total money.
create table core.fx_rate (
    from_currency      char(3) not null,
    to_currency        char(3) not null,
    valid_from         date not null,
    rate               numeric(18,6) not null check (rate > 0),
    source_system      text not null,
    source_raw_id      bigint not null,
    source_changed_at  timestamptz not null,
    last_run_id        bigint not null references ops.pipeline_run,
    is_deleted         boolean not null default false,
    primary key (from_currency, to_currency, valid_from)
);

-- Grain: one group item. The item master is shared across both ERPs in this synthetic group.
create table core.item (
    item_id            text primary key,
    description        text not null,
    item_type          text not null check (item_type in ('GOODS', 'SERVICE')),
    uom                text not null,
    source_system      text not null,
    source_raw_id      bigint not null,
    source_changed_at  timestamptz not null,
    last_run_id        bigint not null references ops.pipeline_run,
    is_deleted         boolean not null default false
);

-- Grain: one cost centre within one company.
create table core.cost_centre (
    company_code       text not null references core.legal_entity,
    cost_centre_code   text not null,
    description        text not null,
    source_system      text not null,
    source_raw_id      bigint not null,
    source_changed_at  timestamptz not null,
    last_run_id        bigint not null references ops.pipeline_run,
    is_deleted         boolean not null default false,
    primary key (company_code, cost_centre_code)
);

-- Grain: one GL account within one company's chart of accounts.
create table core.gl_account (
    company_code       text not null references core.legal_entity,
    gl_account_code    text not null,
    description        text not null,
    source_system      text not null,
    source_raw_id      bigint not null,
    source_changed_at  timestamptz not null,
    last_run_id        bigint not null references ops.pipeline_run,
    is_deleted         boolean not null default false,
    primary key (company_code, gl_account_code)
);

-- Grain: one supplier per source system. Oracle's primary pay site is flattened on; other sites are out of scope.
create table core.supplier (
    source_system       text not null,
    source_supplier_id  text not null,
    company_code        text not null references core.legal_entity,
    supplier_name       text not null,
    address_line        text,
    city                text,
    postcode            text,
    country_code        char(2),
    vat_number          text,
    source_raw_id       bigint not null,
    source_changed_at   timestamptz not null,
    last_run_id         bigint not null references ops.pipeline_run,
    is_deleted          boolean not null default false,
    primary key (source_system, source_supplier_id)
);

-- Grain: one bank account version of one source supplier. History is kept because a recent change is a fraud signal.
create table core.supplier_bank_account (
    source_system       text not null,
    source_supplier_id  text not null,
    valid_from          date not null,
    account_number      text not null,
    source_raw_id       bigint not null,
    source_changed_at   timestamptz not null,
    last_run_id         bigint not null references ops.pipeline_run,
    is_deleted          boolean not null default false,
    primary key (source_system, source_supplier_id, valid_from),
    foreign key (source_system, source_supplier_id) references core.supplier
);

-- Grain: one approval-limit version of one employee. Controls use the version valid on the approval date.
create table core.employee (
    source_system      text not null,
    employee_id        text not null,
    valid_from         date not null,
    valid_to           date not null,
    full_name          text not null,
    company_code       text not null references core.legal_entity,
    approval_limit     numeric(18,2) not null check (approval_limit >= 0),
    currency_code      char(3) not null,
    source_raw_id      bigint not null,
    source_changed_at  timestamptz not null,
    last_run_id        bigint not null references ops.pipeline_run,
    is_deleted         boolean not null default false,
    primary key (source_system, employee_id, valid_from)
);

-- Grain: one version of one price agreement. Tolerance is taken from the version in force at the invoice date.
create table core.contract (
    contract_id         text not null,
    valid_from          date not null,
    valid_to            date not null,
    company_code        text not null references core.legal_entity,
    source_system       text not null,
    source_supplier_id  text not null,
    item_id             text not null references core.item,
    agreed_price        numeric(18,6) not null,
    currency_code       char(3) not null,
    price_tolerance_pct numeric(7,4) not null check (price_tolerance_pct >= 0),
    source_raw_id       bigint not null,
    source_changed_at   timestamptz not null,
    last_run_id         bigint not null references ops.pipeline_run,
    is_deleted          boolean not null default false,
    primary key (contract_id, valid_from),
    foreign key (source_system, source_supplier_id) references core.supplier
);

-- Grain: one requisition. Both source shapes carry a single line per requisition, so no line table is needed.
create table core.purchase_requisition (
    source_system       text not null,
    pr_number           text not null,
    company_code        text not null references core.legal_entity,
    requester_id        text not null,
    source_supplier_id  text,
    item_id             text not null references core.item,
    quantity            numeric(18,3) not null check (quantity > 0),
    unit_price          numeric(18,6) not null check (unit_price >= 0),
    total_amount        numeric(18,2) not null check (total_amount >= 0),
    currency_code       char(3) not null,
    created_date        date not null,
    cost_centre_code    text not null,
    gl_account_code     text not null,
    source_raw_id       bigint not null,
    source_changed_at   timestamptz not null,
    last_run_id         bigint not null references ops.pipeline_run,
    is_deleted          boolean not null default false,
    primary key (source_system, pr_number),
    foreign key (company_code, cost_centre_code) references core.cost_centre,
    foreign key (company_code, gl_account_code) references core.gl_account
);

-- Grain: one approval step on one requisition. Oracle SUBMIT actions are excluded in stg: they are not approvals.
create table core.approval (
    source_system      text not null,
    pr_number          text not null,
    step               integer not null,
    approver_id        text not null,
    approved_at        timestamptz not null,
    source_raw_id      bigint not null,
    source_changed_at  timestamptz not null,
    last_run_id        bigint not null references ops.pipeline_run,
    is_deleted         boolean not null default false,
    primary key (source_system, pr_number, step),
    foreign key (source_system, pr_number) references core.purchase_requisition
);

-- Grain: one purchase order.
create table core.purchase_order (
    source_system       text not null,
    po_number           text not null,
    company_code        text not null references core.legal_entity,
    source_supplier_id  text not null,
    po_date             date not null,
    currency_code       char(3) not null,
    source_raw_id       bigint not null,
    source_changed_at   timestamptz not null,
    last_run_id         bigint not null references ops.pipeline_run,
    is_deleted          boolean not null default false,
    primary key (source_system, po_number),
    foreign key (source_system, source_supplier_id) references core.supplier
);

-- Grain: one purchase order line. pr_number is here, not on the header: a PO can consolidate several requisitions.
-- match_type comes from the source flag (SAP wepos, Oracle match_option), not inferred from the item type.
create table core.po_line (
    source_system     text not null,
    po_number         text not null,
    line_number       integer not null,
    company_code      text not null,
    pr_number         text,
    item_id           text not null references core.item,
    quantity          numeric(18,3) not null check (quantity > 0),
    uom               text not null,
    unit_price        numeric(18,6) not null check (unit_price >= 0),
    cost_centre_code  text not null,
    gl_account_code   text not null,
    contract_id       text,
    match_type        text not null check (match_type in ('THREE_WAY', 'TWO_WAY')),
    source_raw_id     bigint not null,
    source_changed_at timestamptz not null,
    last_run_id       bigint not null references ops.pipeline_run,
    is_deleted        boolean not null default false,
    primary key (source_system, po_number, line_number),
    foreign key (source_system, po_number) references core.purchase_order,
    foreign key (source_system, pr_number) references core.purchase_requisition,
    foreign key (company_code, cost_centre_code) references core.cost_centre,
    foreign key (company_code, gl_account_code) references core.gl_account
);

-- Grain: one goods receipt document. Sources deliver line-level rows; the header is the latest row per document.
create table core.goods_receipt (
    source_system     text not null,
    grn_number        text not null,
    company_code      text not null references core.legal_entity,
    receipt_date      date not null,
    source_raw_id     bigint not null,
    source_changed_at timestamptz not null,
    last_run_id       bigint not null references ops.pipeline_run,
    is_deleted        boolean not null default false,
    primary key (source_system, grn_number)
);

-- Grain: one goods receipt line.
create table core.grn_line (
    source_system     text not null,
    grn_number        text not null,
    line_number       integer not null,
    po_number         text not null,
    po_line_number    integer not null,
    received_quantity numeric(18,3) not null check (received_quantity > 0),
    source_raw_id     bigint not null,
    source_changed_at timestamptz not null,
    last_run_id       bigint not null references ops.pipeline_run,
    is_deleted        boolean not null default false,
    primary key (source_system, grn_number, line_number),
    foreign key (source_system, grn_number) references core.goods_receipt,
    foreign key (source_system, po_number, po_line_number) references core.po_line
);

-- Grain: one supplier invoice or credit note. Amounts are signed: credit notes are negative whichever way the
-- source delivered them. credited_invoice_id has no foreign key: an unresolved reference is an exception to
-- report (UnmatchedCreditNote), not a row to reject.
create table core.supplier_invoice (
    source_system           text not null,
    invoice_id              text not null,
    company_code            text not null references core.legal_entity,
    source_supplier_id      text not null,
    supplier_invoice_number text not null,
    invoice_date            date not null,
    currency_code           char(3) not null,
    net_amount              numeric(18,2) not null,
    document_type           text not null check (document_type in ('INV', 'CRN')),
    credited_invoice_id     text,
    source_raw_id           bigint not null,
    source_changed_at       timestamptz not null,
    last_run_id             bigint not null references ops.pipeline_run,
    is_deleted              boolean not null default false,
    primary key (source_system, invoice_id),
    foreign key (source_system, source_supplier_id) references core.supplier
);

-- Grain: one invoice line. A null po_number is valid data; matching reports it as NoPurchaseOrder.
create table core.invoice_line (
    source_system     text not null,
    invoice_id        text not null,
    line_number       integer not null,
    po_number         text,
    po_line_number    integer,
    invoiced_quantity numeric(18,3) not null,
    unit_price        numeric(18,6) not null,
    net_amount        numeric(18,2) not null,
    source_raw_id     bigint not null,
    source_changed_at timestamptz not null,
    last_run_id       bigint not null references ops.pipeline_run,
    is_deleted        boolean not null default false,
    primary key (source_system, invoice_id, line_number),
    foreign key (source_system, invoice_id) references core.supplier_invoice,
    foreign key (source_system, po_number, po_line_number) references core.po_line
);

create index on core.po_line (source_system, pr_number);
create index on core.grn_line (source_system, po_number, po_line_number);
create index on core.invoice_line (source_system, po_number, po_line_number);
