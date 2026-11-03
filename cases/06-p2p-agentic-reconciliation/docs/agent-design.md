# Agent design

Status: built and evaluated with the offline provider, and acting under each person's database role since milestone 3. Live providers have been tested against mocked responses only. See [decision 0008](decisions/0008-agent-orchestration.md) and [the evaluation](../evidence/agent-evaluation-offline.md).

## Principle

Agents investigate and propose. Deterministic SQL computes matches, tolerances and amounts. Finance users decide. Every step is logged and traceable to source rows and cited document chunks.

```mermaid
flowchart TD
  Q["Invoice line<br/>(recon.current_match_result)"] --> S["Supervisor<br/>reads status, checks access"]
  S -- "other company" --> D["Denied, logged,<br/>no proposal"]
  S --> C["Controls agent<br/>approvals, limits, SoD,<br/>duplicates, bank changes"]
  C --> M["Matching agent<br/>reads SQL results and contract terms"]
  M -- "clean" --> A["Draft AUTO_APPROVE<br/>no model call"]
  M -- "held" --> X["Exception investigator<br/>graph + retrieval + model<br/>at most 4 model steps"]
  A --> G["Guard<br/>re-checks facts and citations"]
  X --> G
  G --> P["agent.proposal<br/>agent.citation"]
  P -- "held" --> H["agent.review_queue<br/>p2p review-list"]
  H --> R["agent.human_decision<br/>p2p review-decide"]
```

## Agents

| Agent | Purpose | Tools (read-only) | Calls a model? | Output |
| --- | --- | --- | --- | --- |
| Supervisor | Routes each line and refuses what the user may not see | `read_queue_item` | No | Status and reasons, or a denial |
| Controls agent | Control findings and the requisition breaches behind them | `check_controls` | No | Findings with row citations |
| Matching agent | SQL result and governing contract version. Never computes an amount | `get_match_result`, `get_contract_terms` | No | Variances with row citations |
| Exception investigator | Explains the cause and proposes an action with evidence | `get_document_chain`, `search_documents` | Yes | Draft proposal |
| Guard | Decides what may be stored, from SQL facts and the run's own tool results | None | No | Proposal and flags |

`search_documents` runs two searches: the rules that apply (policy and procedure, hybrid) and what is on file about the case (hybrid plus graph). In a single list, a supplier's linked contract clauses pushed the governing rule out of the top results: only 59% of held lines cited the labelled rule before this was split.

## Proposed actions

`AUTO_APPROVE` (clean lines only: it means "no review needed", and posts nothing), `NEEDS_HUMAN_REVIEW`, `REQUEST_CORRECTED_INVOICE`, `CHECK_RECEIPT`, `WAIT_FOR_RECEIPT`, `REJECT_DUPLICATE`, `OBTAIN_PURCHASE_ORDER`, `ESCALATE_CONTROL_BREACH`, `VERIFY_BANK_DETAILS`, `REVIEW_CREDIT_NOTE`.

## Access

Synthetic users in `agent.acting_user`, each with a database role: `ap_analyst_uk`, `ap_analyst_de`, `controller` and `auditor`. Each tool runs under `SET LOCAL ROLE` as the acting user, so row-level security applies, and reverts to `agent_service` for logging. Tools also filter by company, and retrieval by access tags, before ranking. A request for another company's invoice looks the same as a request for one that does not exist.

A duplicate can span companies through the golden supplier (S21). The DE01 clerk's evidence says that the original is `withheld: held by another company`, so they know to reject it without seeing another company's invoice. The auditor sees the original.

`agent_service` itself reads no business data, so a tool that forgot to assume a role would fail rather than leak. Neo4j has no row-level security, so its filter stays in the query. See [security](security.md).

## Audit trail

| Table | Grain | Holds |
| --- | --- | --- |
| `agent.run` | One execution for one user | Provider, model, recon run |
| `agent.step` | One node, or one model call | Tokens, whether estimated, cost, latency, outcome, error |
| `agent.tool_call` | One tool invocation | Arguments, result, status (including `denied`), latency |
| `agent.proposal` | One line in one run | SQL status and reasons, drafted and final action, explanation, guard flags |
| `agent.citation` | One thing cited | A chunk id, or a table and primary key |
| `agent.human_decision` | One decision | Who, what, when |
| `agent.audit_log` | One audited event | Written only by triggers: role in use, on behalf of whom, what happened |

## Observability

The trail above is the only source. The governance admin app ([decision 0010](decisions/0010-admin-review-dashboard.md)) reads it as `admin_reviewer` across both companies. Every figure is defined once, in `src/p2p_recon/admin/agents.py`, and the Overview, the Agents page and the audit packs all use that definition.

| Figure | Definition |
| --- | --- |
| Agreement with people | ACCEPT ÷ (ACCEPT + OVERRIDE) on the latest decision per proposal. QUERY counts for neither side |
| Guard intervention | A proposal with any guard flag, or whose stored action differs from the draft. Flags are catalogued with their effect in `guard.FLAGS` |
| Citation validity | Citations that resolve and that the run's acting user may read, checked against that user's scope |
| Latency | p50 and p95 per agent from `agent.step`. Investigator rows include each model call and search in its loop |
| Tokens and cost | As recorded per step. The offline provider's zeros are real zeros |
| Outage routing | Lines flagged `provider_failure` or `step_or_token_limit`, and how many went to a person |

Evaluation runs carry `evaluation` in their parameters and are left out of live figures unless a filter asks for them, because they include providers that misbehave on purpose.

- **Workflow drawing:** the How the agents decide page draws the compiled graph from `workflow.compile_graph`, the one definition `AgentRun` also uses, and shows the path each line of a chosen run took, rebuilt from its steps.
- **Stored results:** `agent-eval` stores a pass or fail per planted line in `ops.evaluation_result`, so results can be compared across runs.
- **Alerts:** thresholds for the Overview's banners, such as citation validity below 100% or any approval with an SQL reason, are in `src/p2p_recon/admin/alerts.yaml`.

## Providers

`LLM_PROVIDER` selects `offline` (default), `anthropic` or `ollama`. `agent-eval` also accepts `hostile`. See [the runbook](runbook.md).

## Evaluation

`uv run p2p agent-eval` runs all planted invoice lines and reports: status and reason agreement, action agreement with labels in `evaluation/expected_actions.yaml`, labelled evidence cited, citation validity, false auto-approvals, latency and cost per invoice, plus hostile-provider, outage and cross-company access runs. Retrieval is evaluated separately by `uv run p2p eval-retrieval`: recall@k and MRR for keyword, vector, hybrid, and hybrid plus graph.
