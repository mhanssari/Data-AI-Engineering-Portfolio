"""The agent graph: supervisor -> controls -> matching -> exception investigator -> proposal.

Only the investigator calls a model. The other nodes are agents in the sense of having their own job and tools, but what
they do is fully specified, so a model would add cost and risk and nothing else. A clean line never reaches the
investigator, which is where most of the cost saving lies. Human review is a queue in PostgreSQL, not a paused graph,
because a finance user's answer can take days and needs no process kept alive for it (decision 0008).
"""

import json
import os
import time
from dataclasses import dataclass
from typing import TypedDict

# LangGraph brings in LangSmith, whose tracing would send prompts and documents off this machine. Keep it off.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import psycopg
from langgraph.graph import END, START, StateGraph
from neo4j import Driver

from p2p_recon.agents.guard import CLEAN_STATUSES, FALLBACK_ACTION, apply_guard
from p2p_recon.agents.providers import TOOLS, DecisionRequest, Provider, ProviderFailure
from p2p_recon.agents.tools import AccessDenied, LineKey, ToolBox, row_citation
from p2p_recon.db import connect
from p2p_recon.retrieval import load_user

MAX_MODEL_STEPS = 4
MAX_TOKENS_PER_LINE = 40_000


class CaseState(TypedDict, total=False):
    key: LineKey
    company_code: str
    status: str
    reasons: list[str]
    match: dict
    exceptions: list[dict]
    breaches: list[dict]
    contracts: list[dict]
    chain: dict | None
    observations: list[dict]
    seen_citations: list[str]
    evidence_ids: list[str]
    drafted_action: str | None
    explanation: str | None
    cited: list[str]
    forced_flags: list[str]
    denied: bool
    proposal_id: int


@dataclass
class LineOutcome:
    key: LineKey
    denied: bool
    proposal_id: int | None
    action: str | None


class AgentRun:
    """One acting user, one provider, any number of invoice lines. Commits as it goes, so that a crash leaves its trail."""

    def __init__(
        self,
        conn: psycopg.Connection,
        db: Driver,
        provider: Provider,
        user_name: str,
        parameters: dict | None = None,
    ):
        self.conn = conn
        self.provider = provider
        self.user = load_user(conn, user_name)
        self.tools = ToolBox(conn, db, self.user)
        self.step_index = 0
        recon_run = conn.execute("select run_id from recon.latest_run").fetchone()
        if recon_run is None:
            raise RuntimeError("No matching run yet. Run `p2p run-all` first.")
        self.run_id = conn.execute(
            "insert into agent.run (provider, model, acting_user, recon_run_id, parameters, status) "
            "values (%s, %s, %s, %s, %s, 'running') returning agent_run_id",
            (provider.name, provider.model, user_name, recon_run["run_id"], json.dumps(parameters or {})),
        ).fetchone()["agent_run_id"]
        self.graph = self._build()

    # ---------------------------------------------------------------- logging
    def _begin_step(self, key: LineKey, agent_name: str) -> int:
        self.step_index += 1
        step_id = self.conn.execute(
            "insert into agent.step (agent_run_id, source_system, invoice_id, line_number, agent_name, step_index) "
            "values (%s, %s, %s, %s, %s, %s) returning step_id",
            (self.run_id, key.source_system, key.invoice_id, key.line_number, agent_name, self.step_index),
        ).fetchone()["step_id"]
        self.tools.step_id = step_id
        return step_id

    def _end_step(
        self, step_id: int, started: float, outcome: object = None, error: str | None = None, decision=None
    ) -> None:
        self.conn.execute(
            """
            update agent.step set latency_ms = %s, outcome = %s, error = %s, input_tokens = %s, output_tokens = %s,
                tokens_estimated = %s, cost_usd = %s
            where step_id = %s
            """,
            (
                round((time.perf_counter() - started) * 1000, 1),
                json.dumps(outcome, default=str),
                error,
                decision.input_tokens if decision else None,
                decision.output_tokens if decision else None,
                decision.tokens_estimated if decision else False,
                decision.cost_usd if decision else None,
                step_id,
            ),
        )

    # ---------------------------------------------------------------- nodes
    def _supervisor(self, state: CaseState) -> CaseState:
        key = state["key"]
        started, step = time.perf_counter(), self._begin_step(key, "supervisor")
        try:
            item = self.tools.read_queue_item(key)
        except AccessDenied as exc:
            self._end_step(step, started, {"route": "denied"}, str(exc))
            return {"denied": True}
        self._end_step(step, started, {"status": item["status_code"], "reasons": item["reasons"]})
        citation = row_citation("recon.match_result", run_id=item["run_id"], **key.as_dict())
        return {
            "denied": False,
            "company_code": item["company_code"],
            "status": item["status_code"],
            "reasons": item["reasons"],
            "seen_citations": [citation],
            "evidence_ids": [citation],
            "observations": [],
            "forced_flags": [],
        }

    def _controls(self, state: CaseState) -> CaseState:
        key = state["key"]
        started, step = time.perf_counter(), self._begin_step(key, "controls")
        result = self.tools.check_controls(key)
        ids = [
            row_citation(
                "recon.control_breach",
                run_id=b["run_id"],
                source_system=b["source_system"],
                pr_number=b["pr_number"],
                reason_code=b["reason_code"],
            )
            for b in result["breaches"]
        ]
        self._end_step(
            step,
            started,
            {
                "findings": [e["reason_code"] for e in result["exceptions"]],
                "breaches": len(result["breaches"]),
            },
        )
        return {
            "breaches": result["breaches"],
            "seen_citations": state["seen_citations"] + ids,
            "evidence_ids": state["evidence_ids"] + ids,
        }

    def _matching(self, state: CaseState) -> CaseState:
        key = state["key"]
        started, step = time.perf_counter(), self._begin_step(key, "matching")
        result = self.tools.get_match_result(key)
        contracts = self.tools.get_contract_terms(key)
        ids = [
            row_citation("recon.exception", run_id=e["run_id"], reason_code=e["reason_code"], **key.as_dict())
            for e in result["exceptions"]
        ] + [
            row_citation("core.contract", contract_id=c["contract_id"], valid_from=c["valid_from"])
            for c in contracts
        ]
        self._end_step(step, started, {"status": result["match"]["status_code"], "contracts": len(contracts)})
        return {
            "match": result["match"],
            "exceptions": result["exceptions"],
            "contracts": contracts,
            "seen_citations": state["seen_citations"] + ids,
            "evidence_ids": state["evidence_ids"] + ids,
        }

    def _case(self, state: CaseState) -> dict:
        return {
            "line": state["key"].as_dict(),
            "company_code": state["company_code"],
            "status": state["status"],
            "reasons": state["reasons"],
            "match": state["match"],
            "exceptions": [
                {"reason_code": e["reason_code"], "evidence": e["evidence"]} for e in state["exceptions"]
            ],
            "breaches": [
                {"pr_number": b["pr_number"], "reason_code": b["reason_code"], "evidence": b["evidence"]}
                for b in state["breaches"]
            ],
            "contract_terms": state["contracts"],
            "document_chain": state.get("chain"),
            "evidence_ids": state["evidence_ids"],
        }

    def _investigator(self, state: CaseState) -> CaseState:
        """A bounded loop. Anything other than a well-formed proposal within the limits ends in human review."""
        key = state["key"]
        started, step = time.perf_counter(), self._begin_step(key, "investigator")
        chain = self.tools.get_document_chain(key)
        self._end_step(step, started, {"chain_found": chain is not None})
        state = {**state, "chain": chain}
        observations: list[dict] = []
        seen = list(state["seen_citations"])
        tokens = 0
        for attempt in range(1, MAX_MODEL_STEPS + 1):
            started, step = time.perf_counter(), self._begin_step(key, "investigator")
            request = DecisionRequest(self._case(state), observations, attempt, MAX_MODEL_STEPS)
            try:
                decision = self.provider.decide(request)
            except ProviderFailure as exc:
                self._end_step(step, started, None, str(exc))
                return {
                    "chain": chain,
                    "observations": observations,
                    "seen_citations": seen,
                    "drafted_action": None,
                    "explanation": None,
                    "cited": [],
                    "forced_flags": ["provider_failure"],
                }
            data = decision.data
            self._end_step(step, started, data, decision=decision)
            tokens += (decision.input_tokens or 0) + (decision.output_tokens or 0)
            next_step = data.get("next")
            if next_step == "propose":
                return {
                    "chain": chain,
                    "observations": observations,
                    "seen_citations": seen,
                    "drafted_action": data.get("action"),
                    "explanation": data.get("explanation"),
                    "cited": data.get("citations") or [],
                }
            if next_step not in TOOLS or not data.get("query"):
                return {
                    "chain": chain,
                    "observations": observations,
                    "seen_citations": seen,
                    "drafted_action": None,
                    "explanation": None,
                    "cited": [],
                    "forced_flags": [f"unknown_tool:{next_step}"],
                }
            if tokens > MAX_TOKENS_PER_LINE:
                break
            started, step = time.perf_counter(), self._begin_step(key, "investigator")
            hits = self.tools.search_documents(data["query"], key)
            self._end_step(step, started, {"hits": [hit["citation"] for hit in hits]})
            observations.append({"query": data["query"], "hits": hits})
            seen += [hit["citation"] for hit in hits]
        return {
            "chain": chain,
            "observations": observations,
            "seen_citations": seen,
            "drafted_action": None,
            "explanation": None,
            "cited": [],
            "forced_flags": ["step_or_token_limit"],
        }

    def _auto_draft(self, state: CaseState) -> CaseState:
        """A clean line needs no model. It still passes through the guard, which re-checks it."""
        return {
            "drafted_action": "AUTO_APPROVE",
            "explanation": f"SQL status is {state['status']} with no exceptions or control findings.",
            "cited": state["evidence_ids"],
        }

    def _submit(self, state: CaseState) -> CaseState:
        key = state["key"]
        started, step = time.perf_counter(), self._begin_step(key, "guard")
        evidence_text = json.dumps(
            {"case": self._case(state), "observations": state.get("observations", [])}, default=str
        )
        guarded = apply_guard(
            state.get("drafted_action"),
            state.get("explanation"),
            state.get("cited", []),
            state["seen_citations"],
            state["evidence_ids"],
            state["status"],
            state["reasons"],
            state["breaches"],
            evidence_text,
        )
        flags = state.get("forced_flags", []) + guarded.flags
        proposal_id = self.conn.execute(
            """
            insert into agent.proposal (agent_run_id, source_system, invoice_id, line_number, company_code, sql_status,
                sql_reasons, drafted_action, proposed_action, explanation, guard_flags)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning proposal_id
            """,
            (
                self.run_id,
                key.source_system,
                key.invoice_id,
                key.line_number,
                state["company_code"],
                state["status"],
                state["reasons"],
                state.get("drafted_action"),
                guarded.action,
                guarded.explanation,
                flags,
            ),
        ).fetchone()["proposal_id"]
        for citation in guarded.citations:
            if citation.startswith("chunk:"):
                self.conn.execute(
                    "insert into agent.citation (proposal_id, kind, chunk_id) values (%s, 'chunk', %s)",
                    (proposal_id, citation.removeprefix("chunk:")),
                )
            else:
                table, cited_key = citation.removeprefix("row:").split("|", 1)
                self.conn.execute(
                    "insert into agent.citation (proposal_id, kind, cited_table, cited_key) values (%s, 'row', %s, %s)",
                    (proposal_id, table, cited_key),
                )
        self._end_step(step, started, {"action": guarded.action, "flags": flags})
        return {"proposal_id": proposal_id, "drafted_action": state.get("drafted_action")}

    # ---------------------------------------------------------------- graph
    def _route_after_supervisor(self, state: CaseState) -> str:
        return "denied" if state["denied"] else "controls"

    def _route_after_matching(self, state: CaseState) -> str:
        clean = state["status"] in CLEAN_STATUSES and not state["reasons"] and not state["breaches"]
        return "auto_draft" if clean else "investigator"

    def _build(self):
        graph = StateGraph(CaseState)
        graph.add_node("supervisor", self._supervisor)
        graph.add_node("controls", self._controls)
        graph.add_node("matching", self._matching)
        graph.add_node("investigator", self._investigator)
        graph.add_node("auto_draft", self._auto_draft)
        graph.add_node("submit", self._submit)
        graph.add_edge(START, "supervisor")
        graph.add_conditional_edges(
            "supervisor", self._route_after_supervisor, {"denied": END, "controls": "controls"}
        )
        graph.add_edge("controls", "matching")
        graph.add_conditional_edges(
            "matching",
            self._route_after_matching,
            {"auto_draft": "auto_draft", "investigator": "investigator"},
        )
        graph.add_edge("investigator", "submit")
        graph.add_edge("auto_draft", "submit")
        graph.add_edge("submit", END)
        return graph.compile()

    def investigate(self, key: LineKey) -> LineOutcome:
        self.step_index = 0
        state = self.graph.invoke({"key": key})
        if state.get("denied"):
            return LineOutcome(key, True, None, None)
        action = self.conn.execute(
            "select proposed_action from agent.proposal where proposal_id = %s", (state["proposal_id"],)
        ).fetchone()["proposed_action"]
        return LineOutcome(key, False, state["proposal_id"], action)

    def finish(self, status: str = "succeeded") -> None:
        self.conn.execute(
            "update agent.run set status = %s, finished_at = now() where agent_run_id = %s",
            (status, self.run_id),
        )


def agent_connection() -> psycopg.Connection:
    """Autocommit, so the trail survives a failure, and under the restricted role, so that a bug cannot write elsewhere."""
    conn = connect(autocommit=True)
    conn.execute("set role p2p_agent")
    return conn


def open_lines(conn: psycopg.Connection, company_codes: list[str], limit: int | None = None) -> list[LineKey]:
    """Every held line for these companies. Clean lines are evaluated separately, since they need no investigation."""
    rows = conn.execute(
        """
        select source_system, invoice_id, line_number from recon.current_match_result
        where company_code = any(%s) and status_code not in ('Matched', 'MatchedWithinTolerance')
        order by source_system, invoice_id, line_number limit %s
        """,
        (company_codes, limit),
    ).fetchall()
    return [LineKey(r["source_system"], r["invoice_id"], r["line_number"]) for r in rows]


__all__ = ["FALLBACK_ACTION", "AgentRun", "LineKey", "LineOutcome", "agent_connection", "open_lines"]
