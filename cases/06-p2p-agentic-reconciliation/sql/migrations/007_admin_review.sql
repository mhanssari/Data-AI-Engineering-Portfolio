-- Governance administrator: reads across both companies, decides nothing, writes only review notes. See decision 0010
-- and docs/security.md. Grants are in sql/routines/50_admin.sql, re-applied on every migrate.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'admin_reviewer') then
        create role admin_reviewer nologin;
    end if;
end;
$$;
grant admin_reviewer to p2p_app with inherit false, set true;

-- Grain: one oversight role that is not a person agents can act for. Kept apart from agent.acting_user on purpose:
-- agent.run.acting_user and agent.human_decision.decided_by both reference acting_user, so a reviewer can be neither
-- the subject of an agent run nor the author of a decision, whatever any grant or policy says.
create table agent.reviewer (
    role_name      text primary key,
    display_name   text not null,
    company_codes  text[] not null,
    access_tags    text[] not null
);
insert into agent.reviewer (role_name, display_name, company_codes, access_tags) values
    ('admin_reviewer', 'Governance administrator', '{GB01,DE01}', '{ap,audit}');

-- Every policy reads visibility through these two functions, so extending them is what lets the reviewer through, and no
-- policy is copied. Still SECURITY INVOKER, for the reason given in migration 006.
create or replace function agent.visible_companies() returns text[] language sql stable as $$
    select coalesce(
        (select company_codes from agent.acting_user where user_name = current_user),
        (select company_codes from agent.reviewer where role_name = current_user),
        '{}'::text[]);
$$;
create or replace function agent.visible_tags() returns text[] language sql stable as $$
    select coalesce(
        (select access_tags from agent.acting_user where user_name = current_user),
        (select access_tags from agent.reviewer where role_name = current_user),
        '{}'::text[]);
$$;

-- Grain: one note by one reviewer on one reviewed object. A follow-up is a new row pointing at the earlier one, never an
-- edit, so the record of what was found and when cannot be tidied up afterwards.
create table agent.admin_review_note (
    note_id      bigint generated always as identity primary key,
    written_by   text not null default current_user,
    written_at   timestamptz not null default clock_timestamp(),
    object_type  text not null check (object_type in (
        'agent_run', 'proposal', 'golden_supplier', 'ontology_term', 'rule', 'contract', 'pipeline_run')),
    object_id    text not null check (btrim(object_id) <> ''),
    category     text not null check (category in (
        'data_quality', 'agent_behaviour', 'citation', 'entity_resolution', 'ontology_gap', 'control_design', 'access',
        'other')),
    note         text not null check (btrim(note) <> ''),
    follow_up    boolean not null default false,
    follows_up   bigint references agent.admin_review_note
);
create index admin_note_object on agent.admin_review_note (object_type, object_id);

-- Readers are decided by grants; the policy only stops anyone writing in another's name.
alter table agent.admin_review_note enable row level security;
create policy note_read on agent.admin_review_note for select using (true);
create policy note_own on agent.admin_review_note for insert to admin_reviewer with check (written_by = current_user);

create trigger admin_note_append_only before update or delete on agent.admin_review_note
    for each row execute function agent.forbid_change();
create trigger admin_note_no_truncate before truncate on agent.admin_review_note
    for each statement execute function agent.forbid_change();

-- The audit log gains one event. The function is replaced whole, because a trigger function has one body.
alter table agent.audit_log drop constraint audit_log_event_check;
alter table agent.audit_log add constraint audit_log_event_check
    check (event in ('agent_run_started', 'proposal_stored', 'human_decision', 'admin_review_note'));

create or replace function agent.write_audit() returns trigger language plpgsql security definer set search_path = agent, pg_temp as $$
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
    elsif tg_table_name = 'admin_review_note' then
        insert into agent.audit_log (event, session_user_name, role_in_use, on_behalf_of, agent_run_id, proposal_id, detail)
        values ('admin_review_note', session_user, current_setting('role'), new.written_by, null,
                case when new.object_type = 'proposal' and new.object_id ~ '^\d+$' then new.object_id::bigint end,
                jsonb_build_object('note_id', new.note_id, 'object_type', new.object_type, 'object_id', new.object_id,
                                   'category', new.category, 'follow_up', new.follow_up, 'follows_up', new.follows_up));
    else
        insert into agent.audit_log (event, session_user_name, role_in_use, on_behalf_of, agent_run_id, proposal_id, detail)
        values ('human_decision', session_user, current_setting('role'), new.decided_by, null, new.proposal_id,
                jsonb_build_object('decision', new.decision, 'note', new.note));
    end if;
    return new;
end;
$$;
create trigger audit_admin_note after insert on agent.admin_review_note for each row execute function agent.write_audit();

-- Grain: one admin page or object viewed. Written only through ops.log_admin_view, which the application calls. That is
-- application-level logging: a caller with database access can read without calling it. docs/security.md says so.
create table ops.admin_view_log (
    view_id            bigint generated always as identity primary key,
    viewed_at          timestamptz not null default clock_timestamp(),
    session_user_name  text not null,
    role_in_use        text not null,
    page               text not null check (btrim(page) <> ''),
    object_type        text,
    object_id          text
);

create trigger admin_view_append_only before update or delete on ops.admin_view_log
    for each row execute function agent.forbid_change();
create trigger admin_view_no_truncate before truncate on ops.admin_view_log
    for each statement execute function agent.forbid_change();

-- SECURITY DEFINER because no role may insert into the log directly. Inside it current_user is the owner, so the caller's
-- role comes from the 'role' setting, and only the reviewer is accepted.
create function ops.log_admin_view(p_page text, p_object_type text default null, p_object_id text default null)
returns bigint language plpgsql security definer set search_path = ops, pg_temp as $$
declare
    new_id bigint;
begin
    if current_setting('role') <> 'admin_reviewer' then
        raise exception 'only admin_reviewer views are logged here' using errcode = 'insufficient_privilege';
    end if;
    insert into ops.admin_view_log (session_user_name, role_in_use, page, object_type, object_id)
    values (session_user, current_setting('role'), p_page, p_object_type, p_object_id)
    returning view_id into new_id;
    return new_id;
end;
$$;
revoke execute on function ops.log_admin_view(text, text, text) from public;
