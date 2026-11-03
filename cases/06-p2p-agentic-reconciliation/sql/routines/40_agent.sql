-- Grants and views over the security model in migration 006. Re-applied on every migrate, because a table added later
-- needs its grant again.

-- People read the canonical model. Row-level security decides which rows; these grants decide which tables.
grant usage on schema core, mdm, recon, docs, agent to ap_analyst_uk, ap_analyst_de, controller, auditor;
grant select on all tables in schema core to ap_analyst_uk, ap_analyst_de, controller, auditor;
grant select on all tables in schema recon to ap_analyst_uk, ap_analyst_de, controller, auditor;
grant select on all tables in schema docs to ap_analyst_uk, ap_analyst_de, controller, auditor;
grant select on mdm.supplier_golden, mdm.supplier_xref to ap_analyst_uk, ap_analyst_de, controller, auditor;
grant select on agent.acting_user, agent.run, agent.step, agent.tool_call, agent.proposal, agent.citation, agent.human_decision
    to ap_analyst_uk, ap_analyst_de, controller, auditor;

-- Nobody but the pipeline reads a full bank account number: not the controller, not the auditor, not an agent. They use
-- core.v_supplier_bank_account_masked.
revoke select on core.supplier_bank_account from ap_analyst_uk, ap_analyst_de, controller, auditor;
grant select on core.v_supplier_bank_account_masked to ap_analyst_uk, ap_analyst_de, controller, auditor;

-- Steward work and the audit log are for oversight roles.
grant select on mdm.supplier_match_candidate, mdm.steward_decision, agent.audit_log to controller, auditor;

-- Recording a decision. The auditor is read-only, and the policy on the table narrows this further.
grant insert on agent.human_decision to ap_analyst_uk, ap_analyst_de, controller;
grant usage on sequence agent.human_decision_decision_id_seq to ap_analyst_uk, ap_analyst_de, controller;

-- agent_service reads no business data of its own. Tools assume the acting user's role for each read, so an agent can see
-- exactly what that person can. This role only writes the trail, with what it needs to start a run.
grant usage on schema core, recon, agent to agent_service;
grant select on core.legal_entity, recon.match_run, recon.latest_run, agent.acting_user to agent_service;
grant select, insert on agent.run, agent.step, agent.tool_call, agent.proposal, agent.citation to agent_service;
grant update (status, finished_at) on agent.run to agent_service;
grant update (input_tokens, output_tokens, tokens_estimated, cost_usd, latency_ms, outcome, error) on agent.step to agent_service;
grant usage on all sequences in schema agent to agent_service;
revoke insert, update, delete on agent.human_decision from agent_service;

-- Grain: one invoice line with an open proposal. A later proposal for the line supersedes an earlier one, and any human
-- decision takes the line off the queue. AUTO_APPROVE proposals need no review, so they never appear, and evaluation
-- runs (which include deliberately misbehaving providers) are kept out of a clerk's work. Runs as the invoker, so that
-- each person's queue is filtered by row-level security.
create or replace view agent.review_queue with (security_invoker = true) as
select p.proposal_id, p.agent_run_id, p.source_system, p.invoice_id, p.line_number, p.company_code, p.sql_status,
       p.sql_reasons, p.proposed_action, p.explanation, p.guard_flags, p.created_at
from (
    select distinct on (pr.source_system, pr.invoice_id, pr.line_number) pr.*
    from agent.proposal pr
    join agent.run r using (agent_run_id)
    where not r.parameters ? 'evaluation'
    order by pr.source_system, pr.invoice_id, pr.line_number, pr.proposal_id desc
) p
where p.requires_review
  and not exists (select 1 from agent.human_decision d where d.proposal_id = p.proposal_id);

grant select on agent.review_queue to ap_analyst_uk, ap_analyst_de, controller, auditor;
