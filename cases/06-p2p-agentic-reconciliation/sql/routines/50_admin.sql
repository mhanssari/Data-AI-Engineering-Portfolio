-- Grants for the governance administrator (migration 007). Re-applied on every migrate, after 40_agent.sql, so that a
-- table added later is covered or, if it should not be, revoked here deliberately.
--
-- Read everything needed to review agents, reconciliation, master data and operations. Row-level security still applies:
-- the reviewer is in agent.reviewer for both companies and both document tags.

grant usage on schema core, mdm, recon, docs, agent, ops to admin_reviewer;
grant select on all tables in schema core to admin_reviewer;
grant select on all tables in schema recon to admin_reviewer;
grant select on all tables in schema docs to admin_reviewer;
grant select on all tables in schema mdm to admin_reviewer;
grant select on agent.acting_user, agent.reviewer, agent.run, agent.step, agent.tool_call, agent.proposal, agent.citation,
    agent.human_decision, agent.review_queue, agent.audit_log, agent.admin_review_note to admin_reviewer;
grant select on ops.pipeline_run, ops.ingest_file, ops.watermark, ops.quarantine, ops.dq_result, ops.lineage_edge,
    ops.schema_migration, ops.admin_view_log to admin_reviewer;

-- The same rule as for every other role: the masked view, never the number.
revoke select on core.supplier_bank_account from admin_reviewer;
grant select on core.v_supplier_bank_account_masked to admin_reviewer;

-- The only writes: review notes, and the view log through its function.
grant insert (object_type, object_id, category, note, follow_up, follows_up) on agent.admin_review_note to admin_reviewer;
grant usage on sequence agent.admin_review_note_note_id_seq to admin_reviewer;
grant execute on function ops.log_admin_view(text, text, text) to admin_reviewer;
-- Where a raw row came from, never its values (migration 008), for the proposal trace.
grant execute on function ops.raw_origin(bigint[]) to admin_reviewer;

-- agent.visible_companies() runs as its caller and reads agent.reviewer, so every role that meets a policy needs to read
-- it, exactly as for agent.acting_user. It holds role names and scopes, nothing else.
grant select on agent.reviewer to ap_analyst_uk, ap_analyst_de, controller, auditor, agent_service;

-- Oversight of the overseer: the auditor reads the notes and the view log; the controller reads the notes.
grant select on agent.admin_review_note, ops.admin_view_log to auditor;
grant usage on schema ops to auditor;
grant select on agent.admin_review_note to controller;
