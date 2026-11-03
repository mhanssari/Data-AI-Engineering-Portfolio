"""Audit packs for one proposal or a date range, as markdown and CSV, built from the same query functions the admin pages
use, so a pack cannot say something the pages do not.

A pack is a snapshot taken by the reviewer: it says when, by whom and against which data-quality run, and quotes model
and document text as stored, inside fenced blocks, so that a markdown viewer cannot render it as anything but text.
"""

import csv
import io
import re
from datetime import UTC, date, datetime

import psycopg

from p2p_recon.admin import agents, notes, overview


def _cell(value: object) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def _fenced(text: str, language: str) -> list[str]:
    """A fence longer than any run of backticks in the text, so the quoted text cannot close it."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{language}", text, fence]


def _table(rows: list[dict], columns: list[str]) -> list[str]:
    if not rows:
        return ["None."]
    return [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
        *("| " + " | ".join(_cell(r.get(c)) for c in columns) + " |" for r in rows),
    ]


def _header(conn: psycopg.Connection, title: str) -> list[str]:
    dq = overview.latest_dq(conn)
    counts = dq["counts"]
    return [
        f"# {title}",
        "",
        f"Exported {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `{conn.execute('select current_user as u').fetchone()['u']}`. "
        "Synthetic data. Read-only: nothing in this pack was decided, posted or paid by its export.",
        "",
        f"Data quality at export (run {dq['run_id']}): {counts['pass']} passed, {counts['warn']} warnings, "
        f"{counts['fail']} failures.",
        "",
    ]


def _csv(rows: list[dict], columns: list[str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def proposal_pack(conn: psycopg.Connection, proposal_id: int) -> dict[str, str] | None:
    """Markdown with the trace, citations, decisions and notes; CSV with one row per citation, decision and note."""
    proposal = conn.execute(
        "select agent_run_id, source_system, invoice_id, line_number from agent.proposal where proposal_id = %s",
        (proposal_id,),
    ).fetchone()
    if proposal is None:
        return None
    detail = agents.line_detail(
        conn,
        proposal["agent_run_id"],
        proposal["source_system"],
        proposal["invoice_id"],
        proposal["line_number"],
    )
    p = detail["proposal"]
    written = notes.notes(conn, "proposal", str(proposal_id))
    lines = _header(conn, f"Audit pack: proposal {proposal_id}")
    lines += [
        "## Proposal",
        "",
        *_table(
            [
                {
                    "run": p["agent_run_id"],
                    "line": f"{p['source_system']} {p['invoice_id']} line {p['line_number']}",
                    "company": p["company_code"],
                    "sql": f"{p['sql_status']} {', '.join(p['sql_reasons'])}".strip(),
                    "drafted": p["drafted_action"],
                    "stored": p["proposed_action"],
                    "guard flags": ", ".join(p["guard_flags"]),
                }
            ],
            ["run", "line", "company", "sql", "drafted", "stored", "guard flags"],
        ),
        "",
        "Explanation, as stored:",
        "",
        *_fenced(p["explanation"], "text"),
        "",
        "## Human decisions",
        "",
        *_table(detail["decisions"], ["decision_id", "decided_at", "decided_by", "decision", "note"]),
        "",
        "## Citations",
        "",
        *_table(
            [
                {
                    "kind": c["kind"],
                    "reference": c["reference"],
                    "resolves": (c["chunk"] if c["kind"] == "chunk" else c["row"]) is not None,
                }
                for c in detail["citations"]
            ],
            ["kind", "reference", "resolves"],
        ),
        "",
        "## Review notes",
        "",
        *_table(
            written,
            ["note_id", "written_at", "written_by", "category", "follow_up", "open", "follows_up", "note"],
        ),
        "",
        "## Trace",
        "",
        *_fenced(agents.trace_markdown(conn, proposal_id), "markdown"),
        "",
    ]
    rows = (
        [
            {"kind": "citation", "id": c["reference"], "value": c["kind"], "detail": c.get("key")}
            for c in detail["citations"]
        ]
        + [
            {
                "kind": "decision",
                "id": d["decision_id"],
                "at": d["decided_at"],
                "who": d["decided_by"],
                "value": d["decision"],
                "detail": d["note"],
            }
            for d in detail["decisions"]
        ]
        + [
            {
                "kind": "note",
                "id": n["note_id"],
                "at": n["written_at"],
                "who": n["written_by"],
                "value": n["category"],
                "detail": n["note"],
            }
            for n in written
        ]
    )
    return {
        "markdown": "\n".join(lines),
        "csv": _csv(
            [{"proposal_id": proposal_id, **r} for r in rows],
            ["proposal_id", "kind", "id", "at", "who", "value", "detail"],
        ),
    }


RANGE_COLUMNS = [
    "proposal_id",
    "agent_run_id",
    "run_started",
    "acting_user",
    "company_code",
    "source_system",
    "invoice_id",
    "line_number",
    "sql_status",
    "sql_reasons",
    "drafted_action",
    "proposed_action",
    "guard_flags",
    "latest_decision",
    "decided_by",
    "decided_at",
    "notes",
    "open_follow_ups",
]


def range_rows(conn: psycopg.Connection, run_filter: agents.RunFilter) -> list[dict]:
    """One row per proposal in the filtered runs, with its latest decision and its notes."""
    run_ids = agents._run_ids(conn, run_filter)
    rows = conn.execute(
        """
        select p.proposal_id, p.agent_run_id, r.started_at as run_started, r.acting_user, p.company_code, p.source_system,
               p.invoice_id, p.line_number, p.sql_status, array_to_string(p.sql_reasons, ', ') as sql_reasons,
               p.drafted_action, p.proposed_action, array_to_string(p.guard_flags, ', ') as guard_flags,
               d.decision as latest_decision, d.decided_by, d.decided_at
        from agent.proposal p
        join agent.run r using (agent_run_id)
        left join lateral (
            select decision, decided_by, decided_at from agent.human_decision h
            where h.proposal_id = p.proposal_id order by h.decision_id desc limit 1
        ) d on true
        where p.agent_run_id = any(%s)
        order by p.proposal_id
        """,
        (run_ids,),
    ).fetchall()
    by_proposal: dict[str, list[dict]] = {}
    for note in notes.notes(conn, "proposal"):
        by_proposal.setdefault(note["object_id"], []).append(note)
    for row in rows:
        written = by_proposal.get(str(row["proposal_id"]), [])
        row["notes"] = len(written)
        row["open_follow_ups"] = sum(1 for n in written if n["open"])
    return rows


def range_pack(conn: psycopg.Connection, run_filter: agents.RunFilter) -> dict[str, str]:
    """Markdown with the figures and the proposals; CSV with every proposal in the range."""
    figures = agents.kpis(conn, run_filter)
    rows = range_rows(conn, run_filter)
    guard, decided, cited = figures["guard"], figures["decisions"], figures["citations"]

    def share(value: float | None) -> str:
        return "–" if value is None else f"{value:.1%}"

    period = f"{run_filter.date_from or 'the first run'} to {run_filter.date_to or 'the latest run'}"
    lines = _header(conn, f"Audit pack: agent runs from {period}")
    lines += [
        f"Runs: {len(figures['run_ids'])}. Evaluation runs {'included' if run_filter.include_evaluation else 'excluded'}.",
        "",
        "## Figures",
        "",
        *_table(
            [
                {
                    "proposals": guard["proposals"],
                    "agreement with people": share(decided["agreement_rate"]),
                    "guard interventions": share(guard["rate"]),
                    "citation validity": share(cited["validity"]),
                    "accepted": decided["counts"]["ACCEPT"],
                    "overridden": decided["counts"]["OVERRIDE"],
                    "queried": decided["counts"]["QUERY"],
                }
            ],
            [
                "proposals",
                "agreement with people",
                "guard interventions",
                "citation validity",
                "accepted",
                "overridden",
                "queried",
            ],
        ),
        "",
        "Definitions as on the Agents page: agreement is ACCEPT ÷ (ACCEPT + OVERRIDE) on the latest decision per proposal; "
        "a guard intervention is a flagged draft or a changed action; a valid citation resolves and is readable by the run's "
        "acting user.",
        "",
        "## Proposals",
        "",
        *_table(
            rows,
            [
                "proposal_id",
                "company_code",
                "invoice_id",
                "line_number",
                "sql_status",
                "proposed_action",
                "latest_decision",
                "notes",
                "open_follow_ups",
            ],
        ),
        "",
    ]
    return {"markdown": "\n".join(lines), "csv": _csv(rows, RANGE_COLUMNS)}


def default_name(prefix: str, date_from: date | None, date_to: date | None) -> str:
    return f"{prefix}-{date_from or 'start'}-to-{date_to or 'latest'}"
