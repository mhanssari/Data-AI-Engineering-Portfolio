"""How the agents behave, read from the trail in `agent`. Definitions live here once, and the Overview and the audit pack
use the same functions, so a figure cannot mean two things.

Definitions:
- A guard intervention is a proposal whose guard flags are not empty, or whose stored action differs from the draft.
- Agreement rate is ACCEPT / (ACCEPT + OVERRIDE) over the latest decision on each proposal. QUERY is a question, not a
  verdict, so it is left out of both sides.
- A citation is valid when it resolves to something that exists and that the run's acting user may read.
- Evaluation runs (run parameters carry 'evaluation') include providers that misbehave on purpose. They are excluded unless
  the filter asks for them, so they never make the live figures look worse or better than they are.
"""

from dataclasses import dataclass, field
from datetime import date

import psycopg

from p2p_recon import lineage
from p2p_recon.admin.masking import mask

CLEAN = ("Matched", "MatchedWithinTolerance")


@dataclass(frozen=True)
class RunFilter:
    date_from: date | None = None
    date_to: date | None = None
    providers: tuple[str, ...] = ()
    users: tuple[str, ...] = ()
    companies: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ()
    include_evaluation: bool = False
    run_ids: tuple[int, ...] = field(default=())


def _where(f: RunFilter) -> tuple[str, dict]:
    """One WHERE clause over agent.run r and agent.acting_user u, with bound parameters only."""
    clauses = ["true"]
    params: dict = {}
    if f.date_from:
        clauses.append("r.started_at >= %(date_from)s")
        params["date_from"] = f.date_from
    if f.date_to:
        clauses.append("r.started_at < %(date_to)s::date + 1")
        params["date_to"] = f.date_to
    if f.providers:
        clauses.append("r.provider = any(%(providers)s)")
        params["providers"] = list(f.providers)
    if f.users:
        clauses.append("r.acting_user = any(%(users)s)")
        params["users"] = list(f.users)
    if f.companies:
        clauses.append("u.company_codes && %(companies)s::text[]")
        params["companies"] = list(f.companies)
    if f.outcomes:
        clauses.append("r.status = any(%(outcomes)s)")
        params["outcomes"] = list(f.outcomes)
    if not f.include_evaluation:
        clauses.append("not r.parameters ? 'evaluation'")
    if f.run_ids:
        clauses.append("r.agent_run_id = any(%(run_ids)s)")
        params["run_ids"] = list(f.run_ids)
    return " and ".join(clauses), params


def filter_options(conn: psycopg.Connection) -> dict:
    return {
        "providers": [
            r["provider"] for r in conn.execute("select distinct provider from agent.run order by 1")
        ],
        "users": [
            r["acting_user"] for r in conn.execute("select distinct acting_user from agent.run order by 1")
        ],
        "companies": [
            r["c"]
            for r in conn.execute(
                "select distinct unnest(company_codes) as c from agent.acting_user order by 1"
            )
        ],
        "outcomes": ["running", "succeeded", "failed"],
    }


def runs(conn: psycopg.Connection, f: RunFilter) -> list[dict]:
    """One row per agent run, newest first, with what it produced."""
    where, params = _where(f)
    return conn.execute(
        f"""
        select r.agent_run_id, r.started_at, r.finished_at, r.provider, r.model, r.acting_user, u.company_codes,
               r.status, r.recon_run_id, r.parameters->>'evaluation' as evaluation,
               count(p.proposal_id) as proposals,
               count(p.proposal_id) filter (where p.proposed_action = 'AUTO_APPROVE') as auto_approved,
               count(p.proposal_id) filter (where p.requires_review) as to_review,
               count(p.proposal_id) filter (where cardinality(p.guard_flags) > 0
                                            or p.drafted_action is distinct from p.proposed_action) as guard_interventions
        from agent.run r
        join agent.acting_user u on u.user_name = r.acting_user
        left join agent.proposal p on p.agent_run_id = r.agent_run_id
        where {where}
        group by r.agent_run_id, u.company_codes
        order by r.agent_run_id desc
        """,
        params,
    ).fetchall()


def _run_ids(conn: psycopg.Connection, f: RunFilter) -> list[int]:
    where, params = _where(f)
    return [
        r["agent_run_id"]
        for r in conn.execute(
            f"select r.agent_run_id from agent.run r join agent.acting_user u on u.user_name = r.acting_user where {where}",
            params,
        )
    ]


def proposals_by_action(conn: psycopg.Connection, run_ids: list[int]) -> list[dict]:
    return conn.execute(
        """
        select proposed_action, sql_status, count(*) as n
        from agent.proposal where agent_run_id = any(%s) group by 1, 2 order by 1, 2
        """,
        (run_ids,),
    ).fetchall()


def decisions(conn: psycopg.Connection, run_ids: list[int]) -> dict:
    """Latest human decision per proposal, and the agreement rate defined in the module docstring."""
    rows = conn.execute(
        """
        select decision, count(*) as n
        from (
            select distinct on (d.proposal_id) d.decision
            from agent.human_decision d join agent.proposal p using (proposal_id)
            where p.agent_run_id = any(%s)
            order by d.proposal_id, d.decision_id desc
        ) latest
        group by 1
        """,
        (run_ids,),
    ).fetchall()
    counts = {d: 0 for d in ("ACCEPT", "OVERRIDE", "QUERY")} | {r["decision"]: r["n"] for r in rows}
    verdicts = counts["ACCEPT"] + counts["OVERRIDE"]
    return {"counts": counts, "agreement_rate": counts["ACCEPT"] / verdicts if verdicts else None}


def guard_interventions(conn: psycopg.Connection, run_ids: list[int]) -> dict:
    """Interventions in total, and each flag's kind (the part before any ':') with how often it fired."""
    total = conn.execute(
        """
        select count(*) as proposals,
               count(*) filter (where cardinality(guard_flags) > 0 or drafted_action is distinct from proposed_action)
                   as interventions,
               count(*) filter (where drafted_action is distinct from proposed_action) as action_changed
        from agent.proposal where agent_run_id = any(%s)
        """,
        (run_ids,),
    ).fetchone()
    reasons = conn.execute(
        """
        select split_part(flag, ':', 1) as reason, count(*) as n
        from agent.proposal, unnest(guard_flags) as flag
        where agent_run_id = any(%s) group by 1 order by 2 desc, 1
        """,
        (run_ids,),
    ).fetchall()
    rate = total["interventions"] / total["proposals"] if total["proposals"] else None
    return {**total, "rate": rate, "reasons": reasons}


def tool_calls(conn: psycopg.Connection, run_ids: list[int]) -> list[dict]:
    return conn.execute(
        """
        select t.tool_name, count(*) as calls,
               count(*) filter (where t.status = 'ok') as ok,
               count(*) filter (where t.status = 'denied') as denied,
               count(*) filter (where t.status = 'error') as errors,
               round(percentile_cont(0.5) within group (order by t.latency_ms)::numeric, 1) as p50_ms
        from agent.tool_call t join agent.step s using (step_id)
        where s.agent_run_id = any(%s) group by 1 order by 1
        """,
        (run_ids,),
    ).fetchall()


def step_latency(conn: psycopg.Connection, run_ids: list[int]) -> list[dict]:
    """p50 and p95 per agent. Investigator rows include every model call and search in its loop, one row each."""
    return conn.execute(
        """
        select agent_name, count(*) as steps,
               round(percentile_cont(0.5) within group (order by latency_ms)::numeric, 1) as p50_ms,
               round(percentile_cont(0.95) within group (order by latency_ms)::numeric, 1) as p95_ms,
               count(*) filter (where error is not null) as errors
        from agent.step where agent_run_id = any(%s) and latency_ms is not null
        group by 1 order by 1
        """,
        (run_ids,),
    ).fetchall()


def usage_by_provider(conn: psycopg.Connection, run_ids: list[int]) -> list[dict]:
    """Tokens and cost as recorded. The offline provider is a rule table: its zeros are real zeros, not missing data."""
    return conn.execute(
        """
        select r.provider, r.model, count(distinct r.agent_run_id) as runs,
               coalesce(sum(s.input_tokens), 0) as input_tokens, coalesce(sum(s.output_tokens), 0) as output_tokens,
               coalesce(sum(s.cost_usd), 0) as cost_usd, bool_or(s.tokens_estimated) as any_estimated
        from agent.run r left join agent.step s using (agent_run_id)
        where r.agent_run_id = any(%s) group by 1, 2 order by 1, 2
        """,
        (run_ids,),
    ).fetchall()


def outage_routing(conn: psycopg.Connection, run_ids: list[int]) -> dict:
    """Lines where the model failed or ran out of steps, and whether every one of them went to a person."""
    row = conn.execute(
        """
        select count(*) filter (where 'provider_failure' = any(guard_flags)) as provider_failures,
               count(*) filter (where 'step_or_token_limit' = any(guard_flags)) as limit_reached,
               count(*) filter (where guard_flags && array['provider_failure', 'step_or_token_limit']
                                and proposed_action = 'NEEDS_HUMAN_REVIEW') as routed_to_person,
               count(*) filter (where guard_flags && array['provider_failure', 'step_or_token_limit']) as affected
        from agent.proposal where agent_run_id = any(%s)
        """,
        (run_ids,),
    ).fetchone()
    return dict(row)


def _citation_valid(conn: psycopg.Connection, citation: dict, scope: dict) -> bool:
    if citation["kind"] == "chunk":
        chunk = conn.execute(
            "select company_code, access_tags from docs.chunk where chunk_id = %s", (citation["chunk_id"],)
        ).fetchone()
        return (
            chunk is not None
            and (chunk["company_code"] == "GROUP" or chunk["company_code"] in scope["company_codes"])
            and bool(set(chunk["access_tags"]) & set(scope["access_tags"]))
        )
    row = lineage.fetch_cited_row(conn, citation)
    if row is None:
        return False
    return "company_code" not in row or row["company_code"] in scope["company_codes"]


def citation_validity(conn: psycopg.Connection, run_ids: list[int]) -> dict:
    """Checked as the reviewer, who sees everything, against the acting user's own scope from agent.acting_user."""
    cited = conn.execute(
        """
        select c.*, u.company_codes, u.access_tags
        from agent.citation c
        join agent.proposal p using (proposal_id)
        join agent.run r using (agent_run_id)
        join agent.acting_user u on u.user_name = r.acting_user
        where p.agent_run_id = any(%s)
        """,
        (run_ids,),
    ).fetchall()
    valid = sum(_citation_valid(conn, c, c) for c in cited)
    return {"citations": len(cited), "valid": valid, "validity": valid / len(cited) if cited else None}


def quality_summary(conn: psycopg.Connection, f: RunFilter) -> dict:
    """The three agent figures the Overview alerts on."""
    run_ids = _run_ids(conn, f)
    guard = guard_interventions(conn, run_ids)
    wrongly_approved = conn.execute(
        """
        select count(*) as n from agent.proposal
        where agent_run_id = any(%s) and proposed_action = 'AUTO_APPROVE'
          and (cardinality(sql_reasons) > 0 or sql_status <> all(%s))
        """,
        (run_ids, list(CLEAN)),
    ).fetchone()["n"]
    return {
        "runs": len(run_ids),
        "citation_validity": citation_validity(conn, run_ids)["validity"],
        "guard_intervention_rate": guard["rate"],
        "auto_approved_with_reasons": wrongly_approved if run_ids else None,
    }


def kpis(conn: psycopg.Connection, f: RunFilter) -> dict:
    run_ids = _run_ids(conn, f)
    return {
        "run_ids": run_ids,
        "proposals": proposals_by_action(conn, run_ids),
        "decisions": decisions(conn, run_ids),
        "guard": guard_interventions(conn, run_ids),
        "citations": citation_validity(conn, run_ids),
        "tools": tool_calls(conn, run_ids),
        "latency": step_latency(conn, run_ids),
        "usage": usage_by_provider(conn, run_ids),
        "outage": outage_routing(conn, run_ids),
    }


# ---------------------------------------------------------------- one run


def run_lines(conn: psycopg.Connection, agent_run_id: int) -> list[dict]:
    """Each invoice line the run looked at, with its proposal if it reached one (a denied line has none)."""
    return conn.execute(
        """
        select s.source_system, s.invoice_id, s.line_number, min(s.step_id) as first_step,
               p.proposal_id, p.company_code, p.sql_status, p.sql_reasons, p.drafted_action, p.proposed_action,
               p.guard_flags
        from agent.step s
        left join agent.proposal p on p.agent_run_id = s.agent_run_id and p.source_system = s.source_system
             and p.invoice_id = s.invoice_id and p.line_number = s.line_number
        where s.agent_run_id = %s
        group by s.source_system, s.invoice_id, s.line_number, p.proposal_id
        order by min(s.step_id)
        """,
        (agent_run_id,),
    ).fetchall()


def line_detail(
    conn: psycopg.Connection, agent_run_id: int, source_system: str, invoice_id: str, line_number: int
) -> dict:
    """The step timeline, tool calls with masked inputs and outputs, citations with their text, proposal and decisions."""
    steps = conn.execute(
        """
        select step_id, step_index, agent_name, started_at, latency_ms, input_tokens, output_tokens, tokens_estimated,
               cost_usd, outcome, error
        from agent.step
        where agent_run_id = %s and source_system = %s and invoice_id = %s and line_number = %s
        order by step_id
        """,
        (agent_run_id, source_system, invoice_id, line_number),
    ).fetchall()
    calls = conn.execute(
        """
        select step_id, tool_call_id, tool_name, status, latency_ms, arguments, result
        from agent.tool_call where step_id = any(%s) order by tool_call_id
        """,
        ([s["step_id"] for s in steps],),
    ).fetchall()
    for step in steps:
        step["outcome"] = mask(step["outcome"])
    for call in calls:
        call["arguments"], call["result"] = mask(call["arguments"]), mask(call["result"])
    proposal = conn.execute(
        """
        select * from agent.proposal
        where agent_run_id = %s and source_system = %s and invoice_id = %s and line_number = %s
        """,
        (agent_run_id, source_system, invoice_id, line_number),
    ).fetchone()
    citations, decided = [], []
    if proposal:
        for citation in conn.execute(
            "select * from agent.citation where proposal_id = %s order by citation_id",
            (proposal["proposal_id"],),
        ):
            if citation["kind"] == "chunk":
                chunk = conn.execute(
                    """
                    select c.section, c.context_header, c.content, d.file_name, d.doc_type
                    from docs.chunk c join docs.document d using (document_id) where c.chunk_id = %s
                    """,
                    (citation["chunk_id"],),
                ).fetchone()
                citations.append({"kind": "chunk", "reference": citation["chunk_id"], "chunk": chunk})
            else:
                row = lineage.fetch_cited_row(conn, citation)
                citations.append(
                    {
                        "kind": "row",
                        "reference": citation["cited_table"],
                        "key": citation["cited_key"],
                        "row": mask(row) if row else None,
                    }
                )
        decided = conn.execute(
            "select decision_id, decision, decided_by, note, decided_at from agent.human_decision where proposal_id = %s order by decision_id",
            (proposal["proposal_id"],),
        ).fetchall()
    return {
        "steps": steps,
        "tool_calls": calls,
        "proposal": proposal,
        "citations": citations,
        "decisions": decided,
    }


def trace_markdown(conn: psycopg.Connection, proposal_id: int) -> str:
    """The same trace `p2p trace` writes, built on the reviewer's own connection."""
    return lineage.render_markdown(lineage.trace_proposal(proposal_id, conn))
