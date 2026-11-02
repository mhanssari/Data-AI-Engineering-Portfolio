-- Retrieval corpus and the agent audit trail. Both are derived or additive: nothing here feeds back into core or recon.

-- Grain: one source document. company_code 'GROUP' means visible to every company.
create table docs.document (
    document_id         text primary key,
    file_name           text not null,
    doc_type            text not null check (doc_type in ('policy', 'procedure', 'contract', 'email', 'note', 'audit_memo')),
    title               text not null,
    company_code        text not null,
    source_system       text,
    source_supplier_id  text,
    supplier_name       text,
    contract_id         text,
    invoice_number      text,
    valid_from          date,
    valid_to            date,
    access_tags         text[] not null,
    content_sha256      text not null,
    last_run_id         bigint not null references ops.pipeline_run
);
create index document_supplier on docs.document (source_system, source_supplier_id);
create index document_contract on docs.document (contract_id);

-- Grain: one clause, section or policy parameter. The id is stable across reloads, so labelled questions and stored
-- citations survive a rebuild. There is no vector index: see decision 0007.
create table docs.chunk (
    chunk_id         text primary key,
    document_id      text not null references docs.document on delete cascade,
    chunk_index      integer not null,
    section          text not null,
    context_header   text not null,
    content          text not null,
    company_code     text not null,
    access_tags      text[] not null,
    embedding        vector(384) not null,
    embedding_model  text not null,
    embedded_sha256  text not null,
    -- The header carries supplier and contract names, which is what lets a keyword search tell near-identical clauses apart.
    search_vector    tsvector generated always as (
        setweight(to_tsvector('english', context_header), 'B') || setweight(to_tsvector('english', content), 'A')
    ) stored
);
create index chunk_search on docs.chunk using gin (search_vector);
create index chunk_document on docs.chunk (document_id);

-- Grain: one person agents can act for. Synthetic, standing in for an identity provider until milestone 3.
create table agent.acting_user (
    user_name      text primary key,
    display_name   text not null,
    company_codes  text[] not null,
    access_tags    text[] not null
);
insert into agent.acting_user (user_name, display_name, company_codes, access_tags) values
    ('ap_gb01', 'Accounts payable clerk, GB01', '{GB01}', '{ap}'),
    ('ap_de01', 'Accounts payable clerk, DE01', '{DE01}', '{ap}'),
    ('auditor', 'Group internal auditor', '{GB01,DE01}', '{ap,audit}');

-- Grain: one execution of the agent graph over a set of invoice lines, for one acting user.
create table agent.run (
    agent_run_id    bigint generated always as identity primary key,
    provider        text not null,
    model           text not null,
    acting_user     text not null references agent.acting_user,
    recon_run_id    bigint not null references recon.match_run,
    parameters      jsonb not null default '{}',
    status          text not null check (status in ('running', 'succeeded', 'failed')),
    started_at      timestamptz not null default now(),
    finished_at     timestamptz
);

-- Grain: one agent node executing once, or one model call in the investigator's loop.
create table agent.step (
    step_id           bigint generated always as identity primary key,
    agent_run_id      bigint not null references agent.run,
    source_system     text not null,
    invoice_id        text not null,
    line_number       integer not null,
    agent_name        text not null,
    step_index        integer not null,
    input_tokens      integer,
    output_tokens     integer,
    tokens_estimated  boolean not null default false,
    cost_usd          numeric(12,6),
    latency_ms        numeric(12,1),
    outcome           jsonb,
    error             text,
    started_at        timestamptz not null default now()
);
create index step_line on agent.step (agent_run_id, source_system, invoice_id, line_number);

-- Grain: one tool invocation. Denied calls are recorded, because an attempt is itself evidence.
create table agent.tool_call (
    tool_call_id  bigint generated always as identity primary key,
    step_id       bigint not null references agent.step,
    tool_name     text not null,
    arguments     jsonb not null,
    result        jsonb,
    status        text not null check (status in ('ok', 'denied', 'error')),
    latency_ms    numeric(12,1) not null,
    created_at    timestamptz not null default now()
);

-- Grain: one proposal for one invoice line in one agent run. No column here can move money or change a status;
-- amounts stay in recon and are reached through citations.
create table agent.proposal (
    proposal_id      bigint generated always as identity primary key,
    agent_run_id     bigint not null references agent.run,
    source_system    text not null,
    invoice_id       text not null,
    line_number      integer not null,
    company_code     text not null,
    sql_status       text not null,
    sql_reasons      text[] not null,
    -- What the model asked for, kept beside what the guard allowed, so that a blocked attempt stays visible.
    drafted_action   text,
    proposed_action  text not null check (proposed_action in (
        'AUTO_APPROVE', 'NEEDS_HUMAN_REVIEW', 'REQUEST_CORRECTED_INVOICE', 'CHECK_RECEIPT', 'WAIT_FOR_RECEIPT',
        'REJECT_DUPLICATE', 'OBTAIN_PURCHASE_ORDER', 'ESCALATE_CONTROL_BREACH', 'VERIFY_BANK_DETAILS', 'REVIEW_CREDIT_NOTE')),
    explanation      text not null,
    guard_flags      text[] not null default '{}',
    requires_review  boolean generated always as (proposed_action <> 'AUTO_APPROVE') stored,
    created_at       timestamptz not null default now(),
    unique (agent_run_id, source_system, invoice_id, line_number)
);

-- Grain: one thing a proposal cites: a document chunk, or a row identified by its table and primary key.
create table agent.citation (
    citation_id   bigint generated always as identity primary key,
    proposal_id   bigint not null references agent.proposal,
    kind          text not null check (kind in ('chunk', 'row')),
    chunk_id      text,
    cited_table   text,
    cited_key     jsonb,
    check ((kind = 'chunk' and chunk_id is not null) or (kind = 'row' and cited_table is not null and cited_key is not null))
);
create index citation_proposal on agent.citation (proposal_id);

-- Grain: one human decision on one proposal; the latest wins. Agents hold no privilege on this table.
create table agent.human_decision (
    decision_id  bigint generated always as identity primary key,
    proposal_id  bigint not null references agent.proposal,
    decision     text not null check (decision in ('ACCEPT', 'OVERRIDE', 'QUERY')),
    decided_by   text not null references agent.acting_user,
    note         text,
    decided_at   timestamptz not null default now()
);

-- The audit trail cannot be rewritten, even by the owner, short of dropping the trigger.
create function agent.forbid_change() returns trigger language plpgsql as $$
begin
    raise exception '% is append-only', tg_table_name;
end;
$$;
create trigger proposal_append_only before update or delete on agent.proposal
    for each row execute function agent.forbid_change();
create trigger citation_append_only before update or delete on agent.citation
    for each row execute function agent.forbid_change();
create trigger decision_append_only before update or delete on agent.human_decision
    for each row execute function agent.forbid_change();

-- The role agent tools run under. NOLOGIN: there is no new credential, the application assumes it with SET ROLE.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'p2p_agent') then
        create role p2p_agent nologin;
    end if;
end;
$$;
