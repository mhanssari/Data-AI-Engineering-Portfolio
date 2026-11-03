"""Evaluation results as stored by eval-retrieval, agent-eval and access-probe in ops.evaluation_run and
ops.evaluation_result. Nothing here re-runs an evaluation or parses the markdown reports in evidence/."""

import psycopg

TREND_MEASURES = {
    "agents": ("action_accuracy", "evidence_hit_rate", "citation_validity", "false_auto_approvals"),
    "retrieval": ("mrr", "recall@5"),
}


def latest_run(conn: psycopg.Connection, kind: str, provider: str | None = None) -> dict | None:
    return conn.execute(
        """
        select evaluation_run_id, kind, provider, model, recorded_at, parameters from ops.evaluation_run
        where kind = %s and (%s::text is null or provider = %s)
        order by evaluation_run_id desc limit 1
        """,
        (kind, provider, provider),
    ).fetchone()


def providers(conn: psycopg.Connection) -> list[str]:
    return [
        r["provider"]
        for r in conn.execute(
            "select distinct provider from ops.evaluation_run where kind = 'agents' order by 1"
        )
    ]


def _results(conn: psycopg.Connection, run_id: int, section: str) -> list[dict]:
    return conn.execute(
        """
        select subject, measure, value, passed, detail from ops.evaluation_result
        where evaluation_run_id = %s and section = %s order by subject, measure
        """,
        (run_id, section),
    ).fetchall()


def _pivot(rows: list[dict], key: str) -> list[dict]:
    """One row per subject with one column per measure, in the order measures first appear."""
    table: dict[str, dict] = {}
    for row in rows:
        table.setdefault(row["subject"], {key: row["subject"]})[row["measure"]] = (
            None if row["value"] is None else float(row["value"])
        )
    return list(table.values())


def retrieval(conn: psycopg.Connection) -> dict:
    """Recall@k, MRR and latency per method; the same by kind of question; and each question's rank per method."""
    run = latest_run(conn, "retrieval")
    if run is None:
        return {"run": None, "methods": [], "by_kind": [], "questions": []}
    run_id = run["evaluation_run_id"]
    questions: dict[str, dict] = {}
    for row in _results(conn, run_id, "retrieval.question"):
        entry = questions.setdefault(
            row["subject"], {"question": row["detail"]["question"], "kind": row["detail"]["kind"]}
        )
        entry[row["measure"]] = None if row["value"] is None else int(row["value"])
    return {
        "run": run,
        "methods": _pivot(_results(conn, run_id, "retrieval"), "method"),
        "by_kind": _pivot(_results(conn, run_id, "retrieval.kind"), "method and kind"),
        "questions": list(questions.values()),
    }


def scenario_matrix(conn: psycopg.Connection, provider: str | None = None) -> dict:
    """Pass or fail per planted line in the latest agent evaluation, with which check failed."""
    run = latest_run(conn, "agents", provider)
    if run is None:
        return {"run": None, "rows": [], "summary": {}}
    rows = []
    for row in _results(conn, run["evaluation_run_id"], "agents.scenario"):
        detail = row["detail"]
        rows.append(
            {
                "scenario": detail["scenario"],
                "line": row["subject"].split(" ", 1)[1],
                "passed": row["passed"],
                "expected_action": detail["expected_action"],
                "action": detail["action"],
                "failed_checks": ", ".join(k for k, ok in detail["checks"].items() if not ok) or None,
                "proposal_id": detail["proposal_id"],
            }
        )
    summary = {
        r["measure"]: float(r["value"])
        for r in _results(conn, run["evaluation_run_id"], "agents.summary")
        if r["value"] is not None
    }
    return {"run": run, "rows": sorted(rows, key=lambda r: (r["passed"], r["scenario"])), "summary": summary}


def robustness(conn: psycopg.Connection, provider: str | None = None) -> list[dict]:
    """The hostile, outage, cross-company and all-lines checks of the latest agent evaluation, with what had to hold."""
    run = latest_run(conn, "agents", provider)
    if run is None:
        return []
    return conn.execute(
        """
        select section, subject, measure, value, passed, detail from ops.evaluation_result
        where evaluation_run_id = %s and section in ('agents.hostile', 'agents.outage', 'agents.access', 'agents.all_lines')
        order by section, measure
        """,
        (run["evaluation_run_id"],),
    ).fetchall()


def access_results(conn: psycopg.Connection) -> dict:
    """The latest access probe: each attempt, what was expected, and what PostgreSQL did."""
    run = latest_run(conn, "access_probe")
    if run is None:
        return {"run": None, "rows": [], "passed": 0, "failed": 0}
    rows = [
        {
            "role": r["subject"].split(" · ", 1)[0],
            "attempt": r["subject"].split(" · ", 1)[1],
            "expected": r["detail"]["expected"],
            "outcome": r["detail"]["outcome"],
            "rows": None if r["value"] is None else int(r["value"]),
            "passed": r["passed"],
            "message": r["detail"]["error"],
        }
        for r in _results(conn, run["evaluation_run_id"], "access")
    ]
    return {
        "run": run,
        "rows": sorted(rows, key=lambda r: (r["passed"], r["attempt"], r["role"])),
        "passed": sum(1 for r in rows if r["passed"]),
        "failed": sum(1 for r in rows if not r["passed"]),
    }


def trend(conn: psycopg.Connection, kind: str) -> list[dict]:
    """Chosen summary figures for every stored run of one kind, oldest first, one row per run and series."""
    # Series names are fixed SQL expressions chosen here, never input.
    if kind == "agents":
        section, series = "agents.summary", "e.provider || ' ' || r.measure"
    else:
        section, series = "retrieval", "r.subject || ' ' || r.measure"
    return conn.execute(
        f"""
        select e.evaluation_run_id, e.recorded_at, e.provider, {series} as series, r.value
        from ops.evaluation_run e join ops.evaluation_result r using (evaluation_run_id)
        where e.kind = %s and r.section = %s and r.measure = any(%s)
        order by e.evaluation_run_id, series
        """,
        (kind, section, list(TREND_MEASURES[kind])),
    ).fetchall()
