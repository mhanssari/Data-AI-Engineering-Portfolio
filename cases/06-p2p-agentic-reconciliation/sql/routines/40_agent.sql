-- Re-applied on every migrate, because a view recreated in another routine or a table added later needs its grant again.

-- Agents read the canonical model and write only to their own trail. No privilege on raw, stg or ops, and none on the
-- bank account number: the column grant leaves it out, so `select *` on that table fails for this role.
grant usage on schema core, mdm, recon, docs, agent to p2p_agent;
grant select on all tables in schema core to p2p_agent;
revoke select on core.supplier_bank_account from p2p_agent;
grant select (source_system, source_supplier_id, valid_from, is_deleted) on core.supplier_bank_account to p2p_agent;
grant select on mdm.supplier_golden, mdm.supplier_xref to p2p_agent;
grant select on all tables in schema recon to p2p_agent;
grant select on all tables in schema docs to p2p_agent;
grant select on all tables in schema agent to p2p_agent;
grant insert on agent.run, agent.step, agent.tool_call, agent.proposal, agent.citation to p2p_agent;
grant update (status, finished_at) on agent.run to p2p_agent;
grant update (input_tokens, output_tokens, tokens_estimated, cost_usd, latency_ms, outcome, error) on agent.step to p2p_agent;
grant usage on all sequences in schema agent to p2p_agent;
revoke insert, update, delete on agent.human_decision from p2p_agent;

-- Grain: one invoice line with an open proposal. A later proposal for the line supersedes an earlier one, and any human
-- decision takes the line off the queue. AUTO_APPROVE proposals need no review, so they never appear, and evaluation
-- runs (which include deliberately misbehaving providers) are kept out of a clerk's work.
create or replace view agent.review_queue as
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
