"""Measure retrieval and the agents, and write what was measured to evidence/.

Labels live in evaluation/, written by hand. Generated data supplies the planted outcomes; nothing here reads a result
to decide what the right answer was.
"""

import csv
import json
import statistics
import time
from collections import Counter
from datetime import date
from importlib.metadata import version
from pathlib import Path

import psycopg
import yaml
from psycopg import sql

from p2p_recon import graph, retrieval
from p2p_recon.agents import workflow
from p2p_recon.agents.guard import CLEAN_STATUSES
from p2p_recon.agents.providers import FailingProvider, HostileProvider, get_provider
from p2p_recon.agents.tools import LineKey
from p2p_recon.config import EVALUATION_DIR, EVIDENCE_DIR, GENERATED_DIR
from p2p_recon.db import connect
from p2p_recon.documents import EMBEDDING_DIMENSION, EMBEDDING_MODEL

K_VALUES = (1, 3, 5)
AP_USER = {"GB01": "ap_gb01", "DE01": "ap_de01"}
OTHER_USER = {"GB01": "ap_de01", "DE01": "ap_gb01"}
# Tables a row citation may point at, with the columns that form each key.
CITABLE = {
    "recon.match_result": ("run_id", "source_system", "invoice_id", "line_number"),
    "recon.exception": ("run_id", "source_system", "invoice_id", "line_number", "reason_code"),
    "recon.control_breach": ("run_id", "source_system", "pr_number", "reason_code"),
    "core.contract": ("contract_id", "valid_from"),
}


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


# ---------------------------------------------------------------- retrieval


def score_rankings(rankings: dict[str, list[str]], expected: dict[str, list[str]]) -> dict:
    """Recall@k as defined in docs/metrics.md: questions with at least one expected chunk in the top k, over all questions."""
    reciprocal = []
    hits = dict.fromkeys(K_VALUES, 0)
    for question_id, chunk_ids in expected.items():
        ranking = rankings[question_id]
        positions = [ranking.index(chunk_id) + 1 for chunk_id in chunk_ids if chunk_id in ranking]
        best = min(positions) if positions else None
        reciprocal.append(1.0 / best if best else 0.0)
        for k in K_VALUES:
            hits[k] += 1 if best and best <= k else 0
    count = len(expected)
    return {
        **{f"recall@{k}": hits[k] / count for k in K_VALUES},
        "mrr": sum(reciprocal) / count,
        "questions": count,
    }


def evaluate_retrieval(
    questions_path: Path = EVALUATION_DIR / "retrieval_questions.yaml", report_dir: Path | None = EVIDENCE_DIR
) -> dict:
    questions = yaml.safe_load(questions_path.read_text(encoding="utf-8"))["questions"]
    results: dict[str, dict] = {}
    detail: dict[str, dict[str, int | None]] = {q["id"]: {} for q in questions}
    with connect() as conn, graph.driver() as db:
        known = {row["chunk_id"] for row in conn.execute("select chunk_id from docs.chunk")}
        stale = sorted({c for q in questions for c in q["expected"] if c not in known})
        if stale:
            raise ValueError(f"Labels name chunks that do not exist: {stale}")
        users = {name: retrieval.load_user(conn, name) for name in {q["user"] for q in questions}}
        chunks = conn.execute(
            "select count(*) as n, count(distinct document_id) as d from docs.chunk"
        ).fetchone()
        for method in retrieval.METHODS:
            rankings = {}
            latencies = []
            for question in questions:
                anchor = question.get("invoice")
                invoice = (anchor["source_system"], anchor["invoice_id"]) if anchor else None
                started = time.perf_counter()
                rankings[question["id"]] = retrieval.ranked_ids(
                    conn, users[question["user"]], question["question"], method, db, invoice
                )
                latencies.append((time.perf_counter() - started) * 1000)
                positions = [
                    rankings[question["id"]].index(c) + 1
                    for c in question["expected"]
                    if c in rankings[question["id"]]
                ]
                detail[question["id"]][method] = min(positions) if positions else None
            scores = score_rankings(rankings, {q["id"]: q["expected"] for q in questions})
            by_kind = {}
            for kind in sorted({q["kind"] for q in questions}):
                subset = {q["id"]: q["expected"] for q in questions if q["kind"] == kind}
                by_kind[kind] = score_rankings(rankings, subset)
            results[method] = {
                **scores,
                "by_kind": by_kind,
                "latency_ms_p50": round(statistics.median(latencies), 1),
                "latency_ms_p95": round(_percentile(latencies, 0.95), 1),
            }
    outcome = {
        "methods": results,
        "chunks": chunks["n"],
        "documents": chunks["d"],
        "questions": questions,
        "positions": detail,
    }
    if report_dir is not None:
        _write_retrieval_report(outcome, report_dir / "retrieval-evaluation.md")
    return {k: v for k, v in outcome.items() if k not in ("questions", "positions")} | {
        "questions": len(questions)
    }


def _write_retrieval_report(outcome: dict, path: Path) -> None:
    methods = outcome["methods"]
    lines = [
        "# Retrieval evaluation",
        "",
        f"**Date:** {date.today().isoformat()}. Written by `uv run p2p eval-retrieval`; do not edit by hand.",
        "",
        f"**Corpus:** {outcome['documents']} synthetic documents, {outcome['chunks']} chunks, default seed. "
        f"**Embedding:** `{EMBEDDING_MODEL}`, {EMBEDDING_DIMENSION} dimensions, through fastembed {version('fastembed')}, exact scan.",
        "",
        f"**Labels:** {len(outcome['questions'])} hand-written questions in `evaluation/retrieval_questions.yaml`. "
        "This set is small and was written by the author of the corpus, so read differences between methods as indicative only. "
        "Latency is one machine and one pass, with the graph on a local container.",
        "",
        "| Method | Recall@1 | Recall@3 | Recall@5 | MRR | p50 ms | p95 ms |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, m in methods.items():
        lines.append(
            f"| {name} | {m['recall@1']:.0%} | {m['recall@3']:.0%} | {m['recall@5']:.0%} | {m['mrr']:.3f} | {m['latency_ms_p50']} | {m['latency_ms_p95']} |"
        )
    lines += [
        "",
        "## By kind of question",
        "",
        "| Kind | Questions | Method | Recall@1 | Recall@5 | MRR |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for kind in sorted(next(iter(methods.values()))["by_kind"]):
        for name, m in methods.items():
            k = m["by_kind"][kind]
            lines.append(
                f"| {kind} | {k['questions']} | {name} | {k['recall@1']:.0%} | {k['recall@5']:.0%} | {k['mrr']:.3f} |"
            )
    lines += [
        "",
        "## Rank of the first expected chunk, per question",
        "",
        "A dash means the method did not return it at all.",
        "",
    ]
    lines += [
        "| Question | Kind | " + " | ".join(methods) + " |",
        "| --- | --- | " + " | ".join("---" for _ in methods) + " |",
    ]
    for question in outcome["questions"]:
        ranks = [outcome["positions"][question["id"]][name] for name in methods]
        lines.append(
            f"| {question['question']} | {question['kind']} | "
            + " | ".join(str(r) if r else "–" for r in ranks)
            + " |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- agents


def planted_lines(expected_path: Path) -> list[dict]:
    """The final expected state of every planted invoice line. Earlier-phase rows describe a state that no longer exists."""
    with expected_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [r for r in rows if r["document_type"] == "INVOICE_LINE" and r["phase"] in ("all", "final")]


def _citation_valid(conn: psycopg.Connection, citation: dict, user: retrieval.ActingUser) -> bool:
    """Does the citation resolve to something that exists and that the acting user may read?"""
    if citation["kind"] == "chunk":
        row = conn.execute(
            f"select 1 from docs.chunk c where c.chunk_id = %(id)s and {retrieval.PERMITTED}",
            {"id": citation["chunk_id"], "companies": user.company_codes, "tags": user.access_tags},
        ).fetchone()
        return row is not None
    columns = CITABLE.get(citation["cited_table"])
    key = citation["cited_key"]
    if columns is None or set(key) != set(columns):
        return False
    schema, table = citation["cited_table"].split(".")
    query = sql.SQL("select 1 from {} where {}").format(
        sql.Identifier(schema, table),
        sql.SQL(" and ").join(sql.SQL("{}::text = %s").format(sql.Identifier(column)) for column in columns),
    )
    return conn.execute(query, [str(key[column]) for column in columns]).fetchone() is not None


def _run_provider(
    provider, lines: list[dict], parameters: dict
) -> tuple[dict[tuple, dict], list[int], dict[tuple, float]]:
    """Investigate each line as its own company's clerk. Returns outcomes, agent run ids and wall-clock per line."""
    outcomes: dict[tuple, dict] = {}
    durations: dict[tuple, float] = {}
    run_ids = []
    with workflow.agent_connection() as conn, graph.driver() as db:
        for company in sorted({line["company_code"] for line in lines}):
            run = workflow.AgentRun(conn, db, provider, AP_USER[company], parameters)
            run_ids.append(run.run_id)
            for line in (line for line in lines if line["company_code"] == company):
                key = LineKey(line["source_system"], line["document_number"], int(line["line_number"]))
                started = time.perf_counter()
                outcome = run.investigate(key)
                durations[(key.source_system, key.invoice_id, key.line_number)] = (
                    time.perf_counter() - started
                )
                outcomes[(key.source_system, key.invoice_id, key.line_number)] = {
                    "outcome": outcome,
                    "line": line,
                }
            run.finish()
    return outcomes, run_ids, durations


def _expected_action(labels: dict, line: dict) -> str:
    status, reasons = line["expected_status"], [r for r in line["expected_reason_codes"].split("|") if r]
    if status in CLEAN_STATUSES:
        return "AUTO_APPROVE"
    if not reasons:
        return labels["by_status"][status]
    return labels["by_reason"][reasons[0]]["action"]


def _expected_evidence(labels: dict, line: dict) -> list[str]:
    reasons = [r for r in line["expected_reason_codes"].split("|") if r]
    if reasons:
        return labels["by_reason"][reasons[0]]["evidence"] + labels.get("by_scenario", {}).get(
            line["scenario_id"], []
        )
    return labels.get("evidence_by_status", {}).get(line["expected_status"], [])


def evaluate_agents(
    provider_name: str | None = None,
    all_lines: bool = False,
    expected_path: Path = GENERATED_DIR / "expected_outcomes.csv",
    labels_path: Path = EVALUATION_DIR / "expected_actions.yaml",
    report_dir: Path | None = EVIDENCE_DIR,
) -> dict:
    labels = yaml.safe_load(labels_path.read_text(encoding="utf-8"))
    lines = planted_lines(expected_path)
    provider = get_provider(provider_name)

    outcomes, run_ids, durations = _run_provider(provider, lines, {"evaluation": "planted"})
    failures: list[dict] = []
    with connect() as conn:
        users = {name: retrieval.load_user(conn, name) for name in AP_USER.values()}
        counts = Counter()
        for item in outcomes.values():
            line, outcome = item["line"], item["outcome"]
            proposal = conn.execute(
                "select * from agent.proposal where proposal_id = %s", (outcome.proposal_id,)
            ).fetchone()
            citations = conn.execute(
                "select kind, chunk_id, cited_table, cited_key from agent.citation where proposal_id = %s",
                (outcome.proposal_id,),
            ).fetchall()
            expected_reasons = sorted(r for r in line["expected_reason_codes"].split("|") if r)
            expected_action = _expected_action(labels, line)
            evidence = _expected_evidence(labels, line)
            cited_chunks = {c["chunk_id"] for c in citations if c["kind"] == "chunk"}
            valid = [_citation_valid(conn, c, users[AP_USER[line["company_code"]]]) for c in citations]

            counts["lines"] += 1
            counts["status_correct"] += proposal["sql_status"] == line["expected_status"]
            counts["reasons_correct"] += sorted(proposal["sql_reasons"]) == expected_reasons
            counts["action_correct"] += proposal["proposed_action"] == expected_action
            counts["citations"] += len(valid)
            counts["citations_valid"] += sum(valid)
            counts["with_citation"] += bool(citations)
            counts["held"] += line["expected_status"] not in CLEAN_STATUSES
            counts["evidence_expected"] += bool(evidence)
            counts["evidence_found"] += bool(set(evidence) & cited_chunks)
            counts["false_auto_approvals"] += (
                proposal["proposed_action"] == "AUTO_APPROVE"
                and line["expected_status"] not in CLEAN_STATUSES
            )
            counts["needs_human"] += proposal["requires_review"]
            if proposal["proposed_action"] != expected_action or (
                evidence and not set(evidence) & cited_chunks
            ):
                failures.append(
                    {
                        "scenario": line["scenario_id"],
                        "invoice": line["document_number"],
                        "expected_action": expected_action,
                        "action": proposal["proposed_action"],
                        "expected_evidence": evidence,
                        "cited_chunks": sorted(cited_chunks),
                    }
                )
        usage = conn.execute(
            """
            select count(*) filter (where agent_name = 'investigator' and (input_tokens is not null)) as model_calls,
                   coalesce(sum(input_tokens), 0) as input_tokens, coalesce(sum(output_tokens), 0) as output_tokens,
                   bool_or(tokens_estimated) as estimated, sum(cost_usd) as cost_usd,
                   count(*) filter (where input_tokens is not null and cost_usd is null) as unpriced_calls
            from agent.step where agent_run_id = any(%s)
            """,
            (run_ids,),
        ).fetchone()
        tool_calls = conn.execute(
            "select count(*) as n from agent.tool_call t join agent.step s using (step_id) where s.agent_run_id = any(%s)",
            (run_ids,),
        ).fetchone()["n"]
        invoices = len({(key[0], key[1]) for key in outcomes})
        per_invoice: dict[tuple, float] = {}
        for key, seconds in durations.items():
            per_invoice[(key[0], key[1])] = per_invoice.get((key[0], key[1]), 0.0) + seconds
        latencies = list(per_invoice.values())

        scope_rows = _outside_agent_scope(expected_path)
        summary = {
            "provider": provider.name,
            "model": provider.model,
            "planted_lines": counts["lines"],
            "invoices": invoices,
            "status_accuracy": counts["status_correct"] / counts["lines"],
            "reason_accuracy": counts["reasons_correct"] / counts["lines"],
            "action_accuracy": counts["action_correct"] / counts["lines"],
            "citation_validity": counts["citations_valid"] / counts["citations"]
            if counts["citations"]
            else 0.0,
            "lines_with_citation": counts["with_citation"],
            "evidence_hit_rate": counts["evidence_found"] / counts["evidence_expected"]
            if counts["evidence_expected"]
            else 0.0,
            "evidence_labelled_lines": counts["evidence_expected"],
            "false_auto_approvals": counts["false_auto_approvals"],
            "sent_to_human_review": counts["needs_human"],
            "model_calls": usage["model_calls"],
            "tool_calls": tool_calls,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "tokens_estimated": bool(usage["estimated"]),
            "cost_usd_total": float(usage["cost_usd"]) if usage["cost_usd"] is not None else None,
            "unpriced_calls": usage["unpriced_calls"],
            "cost_usd_per_invoice": float(usage["cost_usd"]) / invoices
            if usage["cost_usd"] is not None
            else None,
            "latency_s_per_invoice_p50": round(statistics.median(latencies), 3),
            "latency_s_per_invoice_p95": round(_percentile(latencies, 0.95), 3),
        }

    held = [line for line in lines if line["expected_status"] not in CLEAN_STATUSES]
    result = {
        "summary": summary,
        "failures": failures,
        "outside_scope": scope_rows,
        "hostile": _evaluate_hostile(held),
        "outage": _evaluate_outage(held),
        "denials": _evaluate_denials(lines),
        "all_lines": _evaluate_all_lines(provider) if all_lines else None,
    }
    if report_dir is not None:
        _write_agent_report(result, report_dir / f"agent-evaluation-{provider.name}.md")
    return result


def _outside_agent_scope(expected_path: Path) -> dict:
    """Planted rows with no invoice line to investigate. Reported, so that they are not silently dropped."""
    with expected_path.open(encoding="utf-8") as handle:
        rows = [r for r in csv.DictReader(handle) if r["document_type"] != "INVOICE_LINE"]
    return dict(sorted(Counter(r["document_type"] for r in rows).items())) | {
        "scenarios": sorted({r["scenario_id"] for r in rows})
    }


def _evaluate_hostile(held: list[dict]) -> dict:
    """Every held line again, with a provider that attacks the guard. Attempts are counted from the provider's own record."""
    provider = HostileProvider()
    outcomes, run_ids, _ = _run_provider(provider, held, {"evaluation": "hostile"})
    with connect() as conn:
        attacks = conn.execute(
            "select outcome ->> 'attack' as attack, count(*) as attempts from agent.step "
            "where agent_run_id = any(%s) and outcome ? 'attack' group by 1 order by 1",
            (run_ids,),
        ).fetchall()
        proposals = conn.execute(
            "select proposed_action, drafted_action, guard_flags from agent.proposal where agent_run_id = any(%s)",
            (run_ids,),
        ).fetchall()
    return {
        "lines": len(outcomes),
        "attacks": {row["attack"]: row["attempts"] for row in attacks},
        "auto_approve_drafted": sum(1 for p in proposals if p["drafted_action"] == "AUTO_APPROVE"),
        "false_auto_approvals": sum(1 for p in proposals if p["proposed_action"] == "AUTO_APPROVE"),
        "actions": dict(Counter(p["proposed_action"] for p in proposals)),
        "flagged": sum(1 for p in proposals if p["guard_flags"]),
    }


def _evaluate_outage(held: list[dict]) -> dict:
    outcomes, run_ids, _ = _run_provider(FailingProvider(), held, {"evaluation": "outage"})
    with connect() as conn:
        rows = conn.execute(
            "select proposed_action, guard_flags from agent.proposal where agent_run_id = any(%s)", (run_ids,)
        ).fetchall()
    return {
        "lines": len(outcomes),
        "sent_to_human_review": sum(1 for r in rows if r["proposed_action"] == "NEEDS_HUMAN_REVIEW"),
        "flagged_provider_failure": sum(1 for r in rows if "provider_failure" in r["guard_flags"]),
    }


def _evaluate_denials(lines: list[dict]) -> dict:
    """Each company's clerk asks for the other company's lines: every request must be refused, with no proposal."""
    attempted = denied = 0
    proposals = 0
    with workflow.agent_connection() as conn, graph.driver() as db:
        for company, user_name in OTHER_USER.items():
            run = workflow.AgentRun(conn, db, get_provider("offline"), user_name, {"evaluation": "denials"})
            for line in (line for line in lines if line["company_code"] == company):
                attempted += 1
                outcome = run.investigate(
                    LineKey(line["source_system"], line["document_number"], int(line["line_number"]))
                )
                denied += outcome.denied
                proposals += outcome.proposal_id is not None
            run.finish()
    return {"attempted": attempted, "denied": denied, "proposals": proposals}


def _evaluate_all_lines(provider) -> dict:
    """Every line in the current results, as a broader false-approval check against SQL. Background lines are clean by design."""
    with connect() as conn:
        rows = conn.execute(
            "select source_system, invoice_id as document_number, line_number, company_code, status_code from recon.current_match_result"
        ).fetchall()
    outcomes, run_ids, _ = _run_provider(provider, rows, {"evaluation": "all_lines"})
    with connect() as conn:
        proposals = conn.execute(
            "select proposed_action, sql_status, sql_reasons from agent.proposal where agent_run_id = any(%s)",
            (run_ids,),
        ).fetchall()
    return {
        "lines": len(outcomes),
        "auto_approved": sum(1 for p in proposals if p["proposed_action"] == "AUTO_APPROVE"),
        "false_auto_approvals": sum(
            1
            for p in proposals
            if p["proposed_action"] == "AUTO_APPROVE"
            and (p["sql_status"] not in CLEAN_STATUSES or p["sql_reasons"])
        ),
    }


def _write_agent_report(result: dict, path: Path) -> None:
    s = result["summary"]
    h, o, d = result["hostile"], result["outage"], result["denials"]
    cost = (
        f"${s['cost_usd_total']:.4f} in total, ${s['cost_usd_per_invoice']:.4f} per invoice"
        if s["cost_usd_total"] is not None
        else "unknown"
    )
    tokens_note = " (estimated from prompt length; no model was called)" if s["tokens_estimated"] else ""
    reading = (
        "**How to read this run.** The `offline` provider is a rule table written by the same author as the labels, so its action accuracy "
        "shows the pipeline works, not that a model has good judgement. Status and reason accuracy are read from SQL, so they test the "
        "plumbing rather than an agent's reasoning. The results with most meaning here are evidence retrieved, citation validity and the "
        "hostile, outage and access tables, which do not depend on the rule table being clever. The rule table's search phrases were also "
        "written by the corpus's author, so evidence retrieved is an optimistic figure. Cost is zero because nothing was called."
        if s["provider"] == "offline"
        else f"**How to read this run.** Live provider `{s['provider']}`, model `{s['model']}`, one pass. Agreement is with hand-written labels from one author."
    )
    lines = [
        f"# Agent evaluation: {s['provider']} provider",
        "",
        f"**Date:** {date.today().isoformat()}. Written by `uv run p2p agent-eval`; do not edit by hand. **Model:** `{s['model']}`. "
        f"**LangGraph:** {version('langgraph')}. Default seed, synthetic data, local containers on one machine.",
        "",
        reading,
        "",
        "## Planted scenarios",
        "",
        "| Measure | Result |",
        "| --- | --- |",
        f"| Planted invoice lines / invoices | {s['planted_lines']} / {s['invoices']} |",
        f"| Status agrees with expected outcomes (read from SQL) | {s['status_accuracy']:.1%} |",
        f"| Reason codes agree with expected outcomes (read from SQL) | {s['reason_accuracy']:.1%} |",
        f"| Proposed action agrees with the labels | {s['action_accuracy']:.1%} |",
        f"| Lines whose proposal cites the labelled evidence | {s['evidence_hit_rate']:.1%} of {s['evidence_labelled_lines']} labelled |",
        f"| Citations that resolve and the acting user may read | {s['citation_validity']:.1%} |",
        f"| Lines with at least one citation | {s['lines_with_citation']} of {s['planted_lines']} |",
        f"| **False auto-approvals** | **{s['false_auto_approvals']}** |",
        f"| Sent to human review | {s['sent_to_human_review']} |",
        f"| Model calls / tool calls | {s['model_calls']} / {s['tool_calls']} |",
        f"| Tokens in / out | {s['input_tokens']} / {s['output_tokens']}{tokens_note} |",
        f"| Cost | {cost} |",
        f"| Wall-clock per invoice, p50 / p95 | {s['latency_s_per_invoice_p50']} s / {s['latency_s_per_invoice_p95']} s |",
        "",
        "## Hostile provider",
        "",
        "A provider that tries to approve held lines, cite invented or forbidden chunks, name an action or tool that does not exist, and state an invented amount.",
        "",
        f"Lines: {h['lines']}. Drafts asking for AUTO_APPROVE: {h['auto_approve_drafted']}. **False auto-approvals stored: {h['false_auto_approvals']}.** Proposals flagged by the guard: {h['flagged']}.",
        "",
        "| Attack | Attempts |",
        "| --- | --- |",
        *[f"| {name} | {count} |" for name, count in h["attacks"].items()],
        "",
        f"Stored actions: {json.dumps(h['actions'], sort_keys=True)}.",
        "",
        "## Provider outage",
        "",
        f"{o['lines']} held lines with a provider that always fails: {o['sent_to_human_review']} went to human review, {o['flagged_provider_failure']} flagged `provider_failure`.",
        "",
        "## Access",
        "",
        f"Each company's clerk asked for the other company's planted lines: {d['attempted']} attempted, {d['denied']} denied, {d['proposals']} proposals written.",
        "",
        "## Scope",
        "",
        f"Not investigated by agents, because there is no invoice line to investigate: {json.dumps({k: v for k, v in result['outside_scope'].items() if k != 'scenarios'}, sort_keys=True)}, "
        f"from scenarios {', '.join(result['outside_scope']['scenarios'])}. These are covered by the SQL tests.",
    ]
    if result["all_lines"]:
        a = result["all_lines"]
        lines += [
            "",
            f"All current lines: {a['lines']} run, {a['auto_approved']} auto-approved, **{a['false_auto_approvals']} false auto-approvals** against SQL.",
        ]
    if result["failures"]:
        lines += [
            "",
            "## Disagreements with the labels",
            "",
            "| Scenario | Invoice | Expected action | Action | Expected evidence | Cited chunks |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for f in result["failures"]:
            lines.append(
                f"| {f['scenario']} | {f['invoice']} | {f['expected_action']} | {f['action']} | {', '.join(f['expected_evidence'])} | {', '.join(f['cited_chunks'])} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
