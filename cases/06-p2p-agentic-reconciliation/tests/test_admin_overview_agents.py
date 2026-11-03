"""Admin Overview and Agents: the query modules against known data, and the pages through AppTest.

The query tests build one agent run by hand, as the pipeline login, with flags, latencies, citations and decisions chosen
so that every expected figure is known exactly. It is marked as an evaluation run, so it stays out of live figures.
"""

import ast
import json
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from p2p_recon import contracts, graph, lineage
from p2p_recon.admin import agents, overview
from p2p_recon.admin.masking import mask
from p2p_recon.agents.providers import OfflineProvider
from p2p_recon.agents.tools import LineKey
from p2p_recon.agents.workflow import AgentRun, agent_connection
from p2p_recon.db import connect, connect_as

pytestmark = pytest.mark.integration

ADMIN = "admin_reviewer"
SRC = Path(__file__).resolve().parents[1] / "src" / "p2p_recon"
APP = str(SRC / "admin_app.py")
PAGES = (
    "admin_pages/overview.py",
    "admin_pages/agents.py",
    "admin_pages/entities.py",
    "admin_pages/ontology.py",
    "admin_pages/rules.py",
)


# ---------------------------------------------------------------- a run with known figures


@pytest.fixture(scope="module")
def known_run(pipeline, loaded_documents):
    """Four proposals on GB01 lines, by the UK analyst. Two guard interventions, one provider failure."""
    with connect() as conn:
        lines = conn.execute(
            """
            select run_id, source_system, invoice_id, line_number from recon.current_match_result
            where company_code = 'GB01' order by invoice_id, line_number limit 4
            """
        ).fetchall()
        run_id = conn.execute(
            """
            insert into agent.run (provider, model, acting_user, recon_run_id, parameters, status)
            values ('offline', 'rules', 'ap_analyst_uk', %s, '{"evaluation": "admin-figures"}', 'succeeded')
            returning agent_run_id
            """,
            (lines[0]["run_id"],),
        ).fetchone()["agent_run_id"]
        # (drafted, stored, flags): clean; flagged and changed; changed only; provider failure.
        shapes = [
            ("AUTO_APPROVE", "AUTO_APPROVE", []),
            ("AUTO_APPROVE", "NEEDS_HUMAN_REVIEW", ["auto_approve_blocked", "unverified_citations:2"]),
            ("CHECK_RECEIPT", "NEEDS_HUMAN_REVIEW", []),
            (None, "NEEDS_HUMAN_REVIEW", ["provider_failure"]),
        ]
        proposals = []
        for line, (drafted, stored, flags), latency in zip(lines, shapes, (10, 20, 30, 40), strict=True):
            conn.execute(
                """
                insert into agent.step (agent_run_id, source_system, invoice_id, line_number, agent_name, step_index, latency_ms)
                values (%s, %s, %s, %s, 'figures_agent', 1, %s)
                """,
                (run_id, line["source_system"], line["invoice_id"], line["line_number"], latency),
            )
            proposals.append(
                conn.execute(
                    """
                    insert into agent.proposal (agent_run_id, source_system, invoice_id, line_number, company_code, sql_status,
                        sql_reasons, drafted_action, proposed_action, explanation, guard_flags)
                    values (%s, %s, %s, %s, 'GB01', 'Matched', '{}', %s, %s, 'test', %s) returning proposal_id
                    """,
                    (
                        run_id,
                        line["source_system"],
                        line["invoice_id"],
                        line["line_number"],
                        drafted,
                        stored,
                        flags,
                    ),
                ).fetchone()["proposal_id"]
            )
        uk_chunk = conn.execute(
            "select chunk_id from docs.chunk where company_code = 'GB01' and 'ap' = any(access_tags) limit 1"
        ).fetchone()["chunk_id"]
        de_chunk = conn.execute(
            "select chunk_id from docs.chunk where company_code = 'DE01' and 'ap' = any(access_tags) limit 1"
        ).fetchone()["chunk_id"]
        columns = ("run_id", "source_system", "invoice_id", "line_number")
        row_key = {k: lines[0][k] for k in columns}
        de_line = conn.execute(
            f"select {', '.join(columns)} from recon.current_match_result where company_code = 'DE01' limit 1"
        ).fetchone()
        # Valid: the UK chunk and the UK row. Invalid: a DE chunk and a DE row, which exist but the UK analyst may not
        # read, and a chunk that is gone.
        for kind, chunk, table, key in (
            ("chunk", uk_chunk, None, None),
            ("chunk", de_chunk, None, None),
            ("chunk", "no-such-chunk", None, None),
            ("row", None, "recon.match_result", json.dumps(row_key)),
            ("row", None, "recon.match_result", json.dumps(dict(de_line))),
        ):
            conn.execute(
                "insert into agent.citation (proposal_id, kind, chunk_id, cited_table, cited_key) values (%s, %s, %s, %s, %s)",
                (proposals[0], kind, chunk, table, key),
            )
        conn.commit()
    # Decisions by the person who may make them: two accepts, one override, one query.
    with connect_as("ap_analyst_uk") as analyst:
        for proposal, decision in zip(proposals, ("ACCEPT", "ACCEPT", "OVERRIDE", "QUERY"), strict=True):
            analyst.execute(
                "insert into agent.human_decision (proposal_id, decision, decided_by, note) values (%s, %s, 'ap_analyst_uk', 'test')",
                (proposal, decision),
            )
    return {"run_id": run_id, "proposals": proposals, "lines": lines}


def only(known_run):
    return agents.RunFilter(include_evaluation=True, run_ids=(known_run["run_id"],))


def test_agreement_rate_leaves_queries_out_and_the_latest_decision_wins(known_run):
    with connect_as(ADMIN) as conn:
        decided = agents.decisions(conn, [known_run["run_id"]])
    assert decided["counts"] == {"ACCEPT": 2, "OVERRIDE": 1, "QUERY": 1}
    assert decided["agreement_rate"] == pytest.approx(2 / 3)
    # The queried proposal is answered: its latest decision replaces the query.
    with connect_as("ap_analyst_uk") as analyst:
        analyst.execute(
            "insert into agent.human_decision (proposal_id, decision, decided_by, note) values (%s, 'ACCEPT', 'ap_analyst_uk', 'answered')",
            (known_run["proposals"][3],),
        )
    with connect_as(ADMIN) as conn:
        decided = agents.decisions(conn, [known_run["run_id"]])
    assert decided["counts"] == {"ACCEPT": 3, "OVERRIDE": 1, "QUERY": 0}
    assert decided["agreement_rate"] == pytest.approx(3 / 4)


def test_guard_interventions_count_flags_and_changed_actions(known_run):
    with connect_as(ADMIN) as conn:
        guard = agents.guard_interventions(conn, [known_run["run_id"]])
    assert (guard["proposals"], guard["interventions"], guard["action_changed"]) == (4, 3, 3)
    assert guard["rate"] == pytest.approx(0.75)
    assert {r["reason"]: r["n"] for r in guard["reasons"]} == {
        "auto_approve_blocked": 1,
        "unverified_citations": 1,
        "provider_failure": 1,
    }


def test_latency_percentiles(known_run):
    with connect_as(ADMIN) as conn:
        rows = agents.step_latency(conn, [known_run["run_id"]])
    assert [(r["agent_name"], r["steps"], float(r["p50_ms"]), float(r["p95_ms"])) for r in rows] == [
        ("figures_agent", 4, 25.0, 38.5)
    ]


def test_citation_validity_uses_the_acting_users_scope(known_run):
    with connect_as(ADMIN) as conn:
        cited = agents.citation_validity(conn, [known_run["run_id"]])
    assert (cited["citations"], cited["valid"], cited["validity"]) == (5, 2, 0.4)


def test_outage_lines_all_went_to_a_person(known_run):
    with connect_as(ADMIN) as conn:
        outage = agents.outage_routing(conn, [known_run["run_id"]])
    assert outage == {"provider_failures": 1, "limit_reached": 0, "routed_to_person": 1, "affected": 1}


def test_filters_select_runs(known_run):
    with connect_as(ADMIN) as conn:
        assert [r["agent_run_id"] for r in agents.runs(conn, only(known_run))] == [known_run["run_id"]]
        assert known_run["run_id"] not in [r["agent_run_id"] for r in agents.runs(conn, agents.RunFilter())]
        wrong_company = agents.RunFilter(
            include_evaluation=True, run_ids=(known_run["run_id"],), companies=("DE01",)
        )
        assert agents.runs(conn, wrong_company) == []
        row = agents.runs(conn, only(known_run))[0]
    assert (row["proposals"], row["auto_approved"], row["to_review"], row["guard_interventions"]) == (
        4,
        1,
        3,
        3,
    )


def test_impossible_outcomes_are_counted(known_run, conn):
    """The figures behind the 'should be impossible' checks must be able to move: an approval with a reason but a clean
    status, an approval with an unclean status but no reason, and a failed line that did not go to a person."""
    lines = known_run["lines"]
    run_id = conn.execute(
        """
        insert into agent.run (provider, model, acting_user, recon_run_id, parameters, status)
        values ('offline', 'rules', 'ap_analyst_uk', %s, '{"evaluation": "admin-bad"}', 'succeeded') returning agent_run_id
        """,
        (lines[0]["run_id"],),
    ).fetchone()["agent_run_id"]
    for line, status, reasons, action, flags in (
        (lines[0], "Matched", ["PriceVariance"], "AUTO_APPROVE", []),
        (lines[1], "Exception", [], "AUTO_APPROVE", []),
        (lines[2], "Exception", ["PriceVariance"], "CHECK_RECEIPT", ["provider_failure"]),
    ):
        conn.execute(
            """
            insert into agent.proposal (agent_run_id, source_system, invoice_id, line_number, company_code, sql_status,
                sql_reasons, drafted_action, proposed_action, explanation, guard_flags)
            values (%s, %s, %s, %s, 'GB01', %s, %s, %s, %s, 'bad', %s)
            """,
            (
                run_id,
                line["source_system"],
                line["invoice_id"],
                line["line_number"],
                status,
                reasons,
                action,
                action,
                flags,
            ),
        )
    summary = agents.quality_summary(conn, agents.RunFilter(include_evaluation=True, run_ids=(run_id,)))
    assert summary["auto_approved_with_reasons"] == 2
    outage = agents.outage_routing(conn, [run_id])
    assert (outage["affected"], outage["routed_to_person"]) == (1, 0)


# ---------------------------------------------------------------- overview


def test_held_value_is_per_currency_and_matches_recon(pipeline, conn):
    with connect_as(ADMIN) as admin:
        held = overview.held_by_currency(admin)
    expected = conn.execute(
        """
        select company_code, currency_code, count(*) as n, sum(net_amount) as v from recon.current_match_result
        where status_code not in ('Matched', 'MatchedWithinTolerance') group by 1, 2
        """
    ).fetchall()
    assert {(r["company_code"], r["currency_code"]): (r["held_lines"], r["value_on_hold"]) for r in held} == {
        (r["company_code"], r["currency_code"]): (r["n"], r["v"]) for r in expected
    }
    assert len({(r["company_code"], r["currency_code"]) for r in held}) == len(held)


def test_freshness_is_measured_at_the_reference_clock(pipeline, conn):
    with connect_as(ADMIN) as admin:
        rows = overview.freshness(admin)
    assert {r["contract"] for r in rows} == set(contracts.load_contracts())
    for row in rows:
        latest = conn.execute(
            "select max(extracted_at) as t from ops.ingest_file where source_system = any(%s)",
            (row["sources"].split(", "),),
        ).fetchone()["t"]
        assert row["hours_old"] == round((contracts.REFERENCE_CLOCK - latest).total_seconds() / 3600, 1)
        assert row["stale"] is (row["hours_old"] > row["sla_hours"])
    assert not any(r["stale"] for r in rows)


def test_graph_reconciliation_has_every_label_and_type(graph_synced):
    from p2p_recon.ontology import load_mapping

    mapping = load_mapping()
    with connect_as(ADMIN) as admin:
        result = overview.graph_reconciliation(admin)
    assert result["mismatches"] == 0
    assert len(result["rows"]) == len(mapping["classes"]) + len(mapping["relationships"])


def test_dq_summary_matches_the_latest_run(pipeline, dq_results):
    with connect_as(ADMIN) as admin:
        dq = overview.latest_dq(admin)
    assert dq["counts"] == {s: sum(1 for c in dq_results if c.status == s) for s in ("pass", "warn", "fail")}


def test_every_alert_rule_has_a_figure_and_can_fire(graph_synced, monkeypatch):
    rules = overview.alert_rules()
    with connect_as(ADMIN) as admin:
        overview.alerts(admin)  # A rule without a figure would raise KeyError here.
        strict = {
            name: {**rule, **({"max": -1} if "max" in rule else {"min": 2})} for name, rule in rules.items()
        }
        monkeypatch.setattr(overview, "alert_rules", lambda: strict)
        fired = {a.name for a in overview.alerts(admin)}
    # Agent figures are None only when no live agent run exists; everything else must fire under impossible limits.
    assert {"dq_failures", "dq_warnings", "graph_mismatches", "stale_sources", "open_quarantine"} <= fired


def test_ontology_version_is_read_from_the_file(pipeline):
    with connect_as(ADMIN) as admin:
        assert overview.ontology_status(admin)["version"] == "0.2.0"


# ---------------------------------------------------------------- masking and the trace


def test_masking_hides_bank_details_and_keeps_document_numbers():
    masked = mask(
        {
            "iban": "12345678",  # Not IBAN-shaped: masked because of its key alone.
            "note": "Pay GB12FICT123456789012345678 now",
            "invoice_id": "5100000002",
            "nested": [{"account_number": "DE441234"}],
        }
    )
    assert masked["iban"] == "****5678"
    assert "GB12FICT" not in masked["note"] and masked["note"].startswith("Pay ")
    assert masked["invoice_id"] == "5100000002"
    assert masked["nested"][0]["account_number"] == "****1234"


def test_no_bank_number_reaches_a_drill_down(graph_synced, conn):
    with graph.driver() as db, agent_connection() as agent_conn:
        run = AgentRun(agent_conn, db, OfflineProvider(), "ap_analyst_uk", {"evaluation": "admin-mask"})
        run.investigate(LineKey("SAP_LIKE", "5100000002", 1))
        run.finish()
    real = [
        r["account_number"] for r in conn.execute("select account_number from core.supplier_bank_account")
    ]
    with connect_as(ADMIN) as admin:
        detail = agents.line_detail(admin, run.run_id, "SAP_LIKE", "5100000002", 1)
    shown = json.dumps(detail, default=str)
    assert detail["steps"] and detail["proposal"]
    assert not any(number in shown for number in real)


def test_reviewer_trace_is_the_same_as_the_pipelines(graph_synced, known_run):
    proposal = known_run["proposals"][0]
    with connect() as pipeline_conn:
        expected = lineage.render_markdown(lineage.trace_proposal(proposal, pipeline_conn))
    with connect_as(ADMIN) as admin:
        assert agents.trace_markdown(admin, proposal) == expected
    assert "not found" not in expected


@pytest.mark.parametrize("role", ["ap_analyst_uk", "controller", "auditor", "agent_service"])
def test_only_the_reviewer_reads_raw_origins(pipeline, conn, role):
    granted = conn.execute(
        "select has_function_privilege(%s, 'ops.raw_origin(bigint[])', 'execute') as ok", (role,)
    ).fetchone()["ok"]
    assert granted is False


def test_raw_origin_returns_metadata_and_no_values(pipeline, conn):
    raw_id = conn.execute("select raw_id from raw.sap_supplier_bank limit 1").fetchone()["raw_id"]
    with connect_as(ADMIN) as admin:
        rows = admin.execute("select * from ops.raw_origin(%s::bigint[])", ([raw_id],)).fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            admin.execute("select * from raw.sap_supplier_bank limit 1")
    assert [r["raw_table"] for r in rows] == ["raw.sap_supplier_bank"]
    assert set(rows[0]) == {
        "raw_table",
        "raw_id",
        "source_system",
        "batch_id",
        "source_file",
        "file_row",
        "extracted_at",
        "cdc_op",
        "checksum",
    }


# ---------------------------------------------------------------- pages


def app(role: str, page: str | None = None, **params: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=90)
    at.query_params["user"] = role
    for name, value in params.items():
        at.query_params[name] = value
    at.run()
    if page:
        at.switch_page(page).run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_for_the_reviewer(graph_synced, known_run, page):
    at = app(ADMIN, page)
    assert not at.exception, at.exception
    assert not at.error
    assert at.dataframe


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("role", ["ap_analyst_uk", "auditor"])
def test_page_refuses_other_roles(pipeline, page, role):
    at = app(role, page)
    assert not at.exception, at.exception
    assert [e.value for e in at.error] == [
        f"`{role}` cannot open the governance pages. They are for `admin_reviewer` only."
    ]
    assert not at.dataframe and not at.metric


def test_agents_drill_down_shows_the_proposal_trace_and_notes(graph_synced, known_run):
    at = app(ADMIN, "admin_pages/agents.py", run=str(known_run["run_id"]))
    at.toggle(key="agents_evaluation").set_value(True).run()
    assert not at.exception, at.exception
    assert at.selectbox(key="agents_run").value == known_run["run_id"]
    headings = [h.value for h in at.subheader]
    assert f"Proposal {known_run['proposals'][0]}" in headings
    assert "Review notes on this proposal" in headings
    assert any(code.value.startswith(f"# Trace of proposal {known_run['proposals'][0]}") for code in at.code)


def test_a_page_view_is_logged_once_per_session(graph_synced, conn):
    def views() -> int:
        conn.rollback()
        return conn.execute(
            "select count(*) as n from ops.admin_view_log where page = 'overview'"
        ).fetchone()["n"]

    before = views()
    at = app(ADMIN)
    at.run()
    at.run()
    assert views() == before + 1


def test_admin_code_does_not_cache_role_dependent_results():
    """Cached results would be shared across sessions, and so across roles. None of the admin code caches through
    Streamlit; if that changes, the cache key must include the role, and this test must be replaced by one that checks it."""
    files = [*(SRC / "admin").glob("*.py"), *(SRC / "admin_pages").glob("*.py"), SRC / "admin_app.py"]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute):
                assert node.attr not in ("cache_data", "cache_resource"), path.name


def test_admin_code_caches_only_repository_files():
    """lru_cache is used for parsed files from the repository, which are the same for every role. A cached function that
    took a connection, a role or a user could serve one role's rows to another, so none may take one."""
    cached = []
    for path in (SRC / "admin").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and any(
                (d.id if isinstance(d, ast.Name) else getattr(d, "attr", None)) == "lru_cache"
                for d in node.decorator_list
            ):
                cached.append(node.name)
                assert {a.arg for a in node.args.args} <= {"name"}, f"{path.name}:{node.name}"
    assert cached, "expected the parsed-file caches to be found"
