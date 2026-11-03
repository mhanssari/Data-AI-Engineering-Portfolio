-- Roles, row-level security and the audit log. See docs/security.md.
--
-- The pipeline logs in as the superuser from the compose file, which bypasses every policy here. People and agents do
-- not: the application logs in as p2p_app, which has no privileges of its own and may only assume the roles below.

do $$
declare
    role_name text;
begin
    foreach role_name in array array['ap_analyst_uk', 'ap_analyst_de', 'controller', 'auditor', 'agent_service'] loop
        if not exists (select 1 from pg_roles where rolname = role_name) then
            execute format('create role %I nologin', role_name);
        end if;
    end loop;
    -- NOLOGIN here; p2p migrate gives it a login and the password from the environment.
    if not exists (select 1 from pg_roles where rolname = 'p2p_app') then
        create role p2p_app nologin noinherit;
    end if;
end;
$$;

-- SET only, no INHERIT: p2p_app holds nothing until it assumes a role, and it cannot assume the superuser.
grant ap_analyst_uk, ap_analyst_de, controller, auditor, agent_service to p2p_app with inherit false, set true;

-- A policy calls these once per statement, through a scalar subquery. They read current_user, so they must stay
-- SECURITY INVOKER: inside a definer function current_user would be the owner.
create function agent.visible_companies() returns text[] language sql stable as $$
    select coalesce((select company_codes from agent.acting_user where user_name = current_user), '{}'::text[]);
$$;
create function agent.visible_tags() returns text[] language sql stable as $$
    select coalesce((select access_tags from agent.acting_user where user_name = current_user), '{}'::text[]);
$$;

-- Tables that carry company_code themselves.
do $$
declare
    table_name text;
begin
    foreach table_name in array array[
        'core.supplier', 'core.employee', 'core.cost_centre', 'core.gl_account', 'core.contract', 'core.purchase_requisition',
        'core.purchase_order', 'core.po_line', 'core.goods_receipt', 'core.supplier_invoice',
        'recon.control_breach', 'recon.match_result', 'recon.grni_line', 'recon.accrual_journal_line', 'agent.proposal'
    ] loop
        execute format('alter table %s enable row level security', table_name);
        execute format(
            'create policy company_read on %s for select using (company_code = any ((select agent.visible_companies())::text[]))',
            table_name
        );
    end loop;
end;
$$;

-- Tables whose company comes from their parent. The parent's own policy applies inside the subquery, so a row is visible
-- exactly when its parent is, and there is no second copy of company_code to drift.
alter table core.approval enable row level security;
create policy company_read on core.approval for select using (exists (
    select 1 from core.purchase_requisition p where p.source_system = approval.source_system and p.pr_number = approval.pr_number));

alter table core.grn_line enable row level security;
create policy company_read on core.grn_line for select using (exists (
    select 1 from core.goods_receipt p where p.source_system = grn_line.source_system and p.grn_number = grn_line.grn_number));

alter table core.invoice_line enable row level security;
create policy company_read on core.invoice_line for select using (exists (
    select 1 from core.supplier_invoice p where p.source_system = invoice_line.source_system and p.invoice_id = invoice_line.invoice_id));

alter table core.supplier_bank_account enable row level security;
create policy company_read on core.supplier_bank_account for select using (exists (
    select 1 from core.supplier p where p.source_system = supplier_bank_account.source_system
        and p.source_supplier_id = supplier_bank_account.source_supplier_id));

alter table recon.exception enable row level security;
create policy company_read on recon.exception for select using (exists (
    select 1 from recon.match_result p where p.run_id = exception.run_id and p.source_system = exception.source_system
        and p.invoice_id = exception.invoice_id and p.line_number = exception.line_number));

-- The crosswalk would otherwise name another ERP's supplier ids. The golden record itself is group reference data.
alter table mdm.supplier_xref enable row level security;
create policy company_read on mdm.supplier_xref for select using (exists (
    select 1 from core.supplier p where p.source_system = supplier_xref.source_system
        and p.source_supplier_id = supplier_xref.source_supplier_id));

-- Documents: company (or the group), and access tags as well.
alter table docs.document enable row level security;
create policy company_and_tag_read on docs.document for select using (
    (company_code = 'GROUP' or company_code = any ((select agent.visible_companies())::text[]))
    and access_tags && (select agent.visible_tags()));

alter table docs.chunk enable row level security;
create policy company_and_tag_read on docs.chunk for select using (
    (company_code = 'GROUP' or company_code = any ((select agent.visible_companies())::text[]))
    and access_tags && (select agent.visible_tags()));

-- The agent trail. agent_service writes it on behalf of a user; people read only their own companies' part of it.
create policy agent_writes on agent.proposal for all to agent_service using (true) with check (true);

alter table agent.citation enable row level security;
create policy agent_writes on agent.citation for all to agent_service using (true) with check (true);
create policy company_read on agent.citation for select using (exists (
    select 1 from agent.proposal p where p.proposal_id = citation.proposal_id));

alter table agent.step enable row level security;
create policy agent_writes on agent.step for all to agent_service using (true) with check (true);
create policy company_read on agent.step for select using (exists (
    select 1 from core.supplier_invoice i where i.source_system = step.source_system and i.invoice_id = step.invoice_id));

alter table agent.tool_call enable row level security;
create policy agent_writes on agent.tool_call for all to agent_service using (true) with check (true);
create policy company_read on agent.tool_call for select using (exists (
    select 1 from agent.step s where s.step_id = tool_call.step_id));

-- Decisions. The person recorded must be the role in use, the proposal must be one they can see, and a failed control
-- goes to the controller: the clerk who works the queue does not clear a breach. Held in the database, so no interface
-- can skip it. The auditor gets no INSERT at all.
alter table agent.human_decision enable row level security;
create policy company_read on agent.human_decision for select using (exists (
    select 1 from agent.proposal p where p.proposal_id = human_decision.proposal_id));
create policy decide_own on agent.human_decision for insert with check (
    decided_by = current_user
    and exists (
        select 1 from agent.proposal p
        where p.proposal_id = human_decision.proposal_id
          and (current_user = 'controller' or not p.sql_reasons && array[
              'ApprovalLimitExceeded', 'SegregationOfDuties', 'MissingApproval', 'SplitRequisition', 'RetrospectivePO',
              'MissingRequisition'])));

-- Grain: one audited event. Written only by triggers, so an actor cannot leave an action out or word it differently.
create table agent.audit_log (
    audit_id      bigint generated always as identity primary key,
    occurred_at   timestamptz not null default clock_timestamp(),
    event         text not null check (event in ('agent_run_started', 'proposal_stored', 'human_decision')),
    session_user_name text not null,
    role_in_use   text not null,
    on_behalf_of  text,
    agent_run_id  bigint,
    proposal_id   bigint,
    detail        jsonb not null
);
create index audit_proposal on agent.audit_log (proposal_id);

-- SECURITY DEFINER because no role may insert here directly. Inside it current_user is the owner, so the role comes from
-- the 'role' setting.
create function agent.write_audit() returns trigger language plpgsql security definer set search_path = agent, pg_temp as $$
begin
    if tg_table_name = 'run' then
        insert into agent.audit_log (event, session_user_name, role_in_use, on_behalf_of, agent_run_id, proposal_id, detail)
        values ('agent_run_started', session_user, current_setting('role'), new.acting_user, new.agent_run_id, null,
                jsonb_build_object('provider', new.provider, 'model', new.model, 'recon_run_id', new.recon_run_id));
    elsif tg_table_name = 'proposal' then
        insert into agent.audit_log (event, session_user_name, role_in_use, on_behalf_of, agent_run_id, proposal_id, detail)
        values ('proposal_stored', session_user, current_setting('role'),
                (select r.acting_user from agent.run r where r.agent_run_id = new.agent_run_id),
                new.agent_run_id, new.proposal_id,
                jsonb_build_object('invoice_id', new.invoice_id, 'line_number', new.line_number, 'sql_status', new.sql_status,
                                   'drafted_action', new.drafted_action, 'proposed_action', new.proposed_action,
                                   'guard_flags', new.guard_flags));
    else
        insert into agent.audit_log (event, session_user_name, role_in_use, on_behalf_of, agent_run_id, proposal_id, detail)
        values ('human_decision', session_user, current_setting('role'), new.decided_by, null, new.proposal_id,
                jsonb_build_object('decision', new.decision, 'note', new.note));
    end if;
    return new;
end;
$$;
create trigger audit_run after insert on agent.run for each row execute function agent.write_audit();
create trigger audit_proposal after insert on agent.proposal for each row execute function agent.write_audit();
create trigger audit_decision after insert on agent.human_decision for each row execute function agent.write_audit();

create trigger audit_append_only before update or delete on agent.audit_log
    for each row execute function agent.forbid_change();
-- Row triggers do not fire on TRUNCATE, which would otherwise empty the trail without complaint.
create trigger audit_no_truncate before truncate on agent.audit_log
    for each statement execute function agent.forbid_change();
create trigger proposal_no_truncate before truncate on agent.proposal
    for each statement execute function agent.forbid_change();
create trigger citation_no_truncate before truncate on agent.citation
    for each statement execute function agent.forbid_change();
create trigger decision_no_truncate before truncate on agent.human_decision
    for each statement execute function agent.forbid_change();
