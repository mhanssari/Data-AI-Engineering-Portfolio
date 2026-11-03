"""Overview figures for the governance reviewer. Every figure reads something already stored by a pipeline step; nothing
here recomputes a result, so the page shows what the pipeline recorded, not a second opinion of it."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import psycopg
import yaml
from rdflib import OWL, Graph

from p2p_recon import contracts
from p2p_recon.admin import agents
from p2p_recon.config import ONTOLOGY_DIR

ALERTS_PATH = Path(__file__).with_name("alerts.yaml")


@lru_cache
def alert_rules() -> dict:
    return yaml.safe_load(ALERTS_PATH.read_text(encoding="utf-8"))["alerts"]


def recent_runs(conn: psycopg.Connection, limit: int = 15) -> list[dict]:
    return conn.execute(
        """
        select run_id, step, status, started_at, finished_at,
               round(extract(epoch from finished_at - started_at)::numeric, 1) as seconds, error_message
        from ops.pipeline_run order by run_id desc limit %s
        """,
        (limit,),
    ).fetchall()


def latest_dq(conn: psycopg.Connection) -> dict:
    """The latest `dq` run: counts by status and the checks that did not pass. Graph checks are reported separately."""
    run = conn.execute(
        "select max(run_id) as run_id from ops.pipeline_run where step = 'dq' and status = 'succeeded'"
    ).fetchone()["run_id"]
    if run is None:
        return {"run_id": None, "counts": {"pass": 0, "warn": 0, "fail": 0}, "not_passing": []}
    rows = conn.execute(
        "select check_name, scope, expected, actual, status, detail from ops.dq_result where run_id = %s order by check_name, scope",
        (run,),
    ).fetchall()
    counts = {status: sum(1 for r in rows if r["status"] == status) for status in ("pass", "warn", "fail")}
    return {"run_id": run, "counts": counts, "not_passing": [r for r in rows if r["status"] != "pass"]}


def graph_reconciliation(conn: psycopg.Connection) -> dict:
    """Neo4j against PostgreSQL, as the latest graph sync recorded it: one row per label and per relationship type."""
    run = conn.execute(
        "select max(run_id) as run_id from ops.pipeline_run where step = 'graph_sync' and status = 'succeeded'"
    ).fetchone()["run_id"]
    rows = conn.execute(
        """
        select case when check_name like 'graph.nodes.%%' then 'node' else 'relationship' end as kind,
               regexp_replace(check_name, '^graph\\.(nodes|edges)\\.', '') as name, expected, actual, status
        from ops.dq_result where run_id = %s and check_name like 'graph.%%' order by 1, 2
        """,
        (run,),
    ).fetchall()
    return {"run_id": run, "rows": rows, "mismatches": sum(1 for r in rows if r["status"] != "pass")}


def freshness(conn: psycopg.Connection, as_of: datetime = contracts.REFERENCE_CLOCK) -> list[dict]:
    """Hours since each contract's newest source extract, against that contract's SLA, at the reference clock."""
    rows = []
    for name, contract in contracts.load_contracts().items():
        spec = contract["freshness"]
        latest = conn.execute(
            "select max(extracted_at) as t from ops.ingest_file where source_system = any(%s)",
            (spec["source_systems"],),
        ).fetchone()["t"]
        hours = None if latest is None else round((as_of - latest).total_seconds() / 3600, 1)
        rows.append(
            {
                "contract": name,
                "sources": ", ".join(spec["source_systems"]),
                "latest_extract": latest,
                "hours_old": hours,
                "sla_hours": spec["sla_hours"],
                "stale": hours is None or hours > spec["sla_hours"],
            }
        )
    return rows


def open_quarantine(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        """
        select entity, source_system, reason, count(*) as n
        from ops.quarantine where resolved_run_id is null group by 1, 2, 3 order by 1, 2, 3
        """
    ).fetchall()


def held_by_currency(conn: psycopg.Connection) -> list[dict]:
    """Held lines and their value, one row per company and currency. Amounts are never added across currencies."""
    return conn.execute(
        """
        select company_code, currency_code, count(*) as held_lines, sum(net_amount) as value_on_hold
        from recon.current_match_result
        where status_code not in ('Matched', 'MatchedWithinTolerance')
        group by 1, 2 order by 1, 2
        """
    ).fetchall()


def review_queue(conn: psycopg.Connection) -> dict:
    """Open work by proposed action, and decisions made so far by kind (the latest decision on each proposal)."""
    open_by_action = conn.execute(
        "select proposed_action, count(*) as n from agent.review_queue group by 1 order by 2 desc, 1"
    ).fetchall()
    decided = conn.execute(
        """
        select decision, count(*) as n
        from (select distinct on (proposal_id) decision from agent.human_decision order by proposal_id, decision_id desc) d
        group by 1 order by 1
        """
    ).fetchall()
    return {"open_by_action": open_by_action, "decided": decided}


def ontology_status(conn: psycopg.Connection) -> dict:
    """The ontology's declared version, and how many sampled real chains conformed in the last recorded validation."""
    graph = Graph().parse(ONTOLOGY_DIR / "p2p.ttl")
    version = next((str(v) for v in graph.objects(None, OWL.versionInfo)), None)
    run = conn.execute(
        "select max(run_id) as run_id from ops.pipeline_run where step = 'validate_ontology' and status = 'succeeded'"
    ).fetchone()["run_id"]
    if run is None:
        return {"version": version, "shacl_run_id": None, "checked": 0, "conforming": 0}
    rows = conn.execute(
        "select status from ops.dq_result where run_id = %s and check_name like 'shacl.chain.%%'", (run,)
    ).fetchall()
    return {
        "version": version,
        "shacl_run_id": run,
        "checked": len(rows),
        "conforming": sum(1 for r in rows if r["status"] == "pass"),
    }


@dataclass(frozen=True)
class Alert:
    name: str
    severity: str
    description: str
    actual: object
    limit: object


def alerts(conn: psycopg.Connection) -> list[Alert]:
    """Every threshold in alerts.yaml that the stored figures breach. An empty list means nothing to report."""
    rules = alert_rules()
    quality = agents.quality_summary(conn, agents.RunFilter())
    dq = latest_dq(conn)
    figures: dict[str, object] = {
        "dq_failures": dq["counts"]["fail"],
        "dq_warnings": dq["counts"]["warn"],
        "graph_mismatches": graph_reconciliation(conn)["mismatches"],
        "stale_sources": sum(1 for row in freshness(conn) if row["stale"]),
        "open_quarantine": sum(row["n"] for row in open_quarantine(conn)),
        "citation_validity": quality["citation_validity"],
        "guard_intervention_rate": quality["guard_intervention_rate"],
        "auto_approved_with_reasons": quality["auto_approved_with_reasons"],
    }
    raised = []
    for name, rule in rules.items():
        actual = figures[name]
        if actual is None:  # Nothing to measure yet, such as no agent runs: not an alert.
            continue
        actual = float(actual) if isinstance(actual, Decimal) else actual
        if "max" in rule and actual > rule["max"]:
            raised.append(Alert(name, rule["severity"], rule["description"], actual, rule["max"]))
        if "min" in rule and actual < rule["min"]:
            raised.append(Alert(name, rule["severity"], rule["description"], actual, rule["min"]))
    return raised
