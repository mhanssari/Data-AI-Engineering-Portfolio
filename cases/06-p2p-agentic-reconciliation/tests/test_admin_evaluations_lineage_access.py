"""Admin Evaluations, Lineage and contracts, Access and audit, and the audit pack.

Stored evaluation rows are compared with what the evaluation returned; the permission matrix with the contracts' access
promises; the audit pack with the query functions it is built from.
"""

import copy
import csv
import io
import re

import pytest

from p2p_recon import access_probe, contracts, evaluation
from p2p_recon.admin import access, agents, audit_pack, datasets, evaluations, notes, overview
from p2p_recon.admin.masking import SENSITIVE_TEXT
from p2p_recon.agents import workflow
from p2p_recon.agents.guard import GuardResult
from p2p_recon.agents.providers import ACTIONS
from p2p_recon.db import connect, connect_as

pytestmark = pytest.mark.integration

ADMIN = "admin_reviewer"


# ---------------------------------------------------------------- evaluations


@pytest.fixture(scope="module")
def evaluated(graph_synced, pipeline):
    return evaluation.evaluate_agents(
        "offline", expected_path=pipeline.data_dir / "expected_outcomes.csv", report_dir=None
    )


def test_agent_evaluation_is_stored_as_it_was_returned(evaluated):
    with connect_as(ADMIN) as admin:
        matrix = evaluations.scenario_matrix(admin, "offline")
        checks = {(r["section"], r["measure"]): r for r in evaluations.robustness(admin, "offline")}
    assert matrix["run"]["evaluation_run_id"] == evaluated["evaluation_run_id"]
    summary = evaluated["summary"]
    assert len(matrix["rows"]) == summary["planted_lines"]
    for name in ("action_accuracy", "citation_validity", "evidence_hit_rate", "false_auto_approvals"):
        assert matrix["summary"][name] == pytest.approx(summary[name]), name
    failed = {(r["scenario"], r["line"].split(":")[1]) for r in matrix["rows"] if not r["passed"]}
    assert {(f["scenario"], f["invoice"]) for f in evaluated["failures"]} <= failed
    hostile = checks[("agents.hostile", "false_auto_approvals")]
    assert hostile["value"] == evaluated["hostile"]["false_auto_approvals"] == 0 and hostile["passed"]
    denied = checks[("agents.access", "denied")]
    assert denied["value"] == evaluated["denials"]["attempted"] and denied["passed"]
    assert checks[("agents.outage", "sent_to_human_review")]["passed"]


def test_a_failed_check_is_stored_as_failed(pipeline, graph_synced, evaluated, monkeypatch):
    """With the guard disabled, the hostile provider's false approvals must be stored as failures, not as passes."""

    def no_guard(drafted_action, explanation, cited, seen_citations, *rest):
        action = drafted_action if drafted_action in ACTIONS else "NEEDS_HUMAN_REVIEW"
        return GuardResult(action, explanation or "unguarded", seen_citations[:1], [])

    monkeypatch.setattr(workflow, "apply_guard", no_guard)
    result = evaluation.evaluate_agents(
        "hostile", expected_path=pipeline.data_dir / "expected_outcomes.csv", report_dir=None
    )
    with connect_as(ADMIN) as admin:
        matrix = evaluations.scenario_matrix(admin, "hostile")
    assert result["summary"]["false_auto_approvals"] > 0
    assert matrix["summary"]["false_auto_approvals"] == result["summary"]["false_auto_approvals"]
    assert any(not r["passed"] and "no_false_approval" in (r["failed_checks"] or "") for r in matrix["rows"])
    with connect_as(ADMIN) as admin:
        hostile = next(
            r
            for r in evaluations.robustness(admin, "hostile")
            if (r["section"], r["measure"]) == ("agents.hostile", "false_auto_approvals")
        )
    assert hostile["value"] > 0 and hostile["passed"] is False
    # A later run by another provider does not replace the offline one on the page.
    with connect_as(ADMIN) as admin:
        assert (
            evaluations.scenario_matrix(admin, "offline")["run"]["evaluation_run_id"]
            == evaluated["evaluation_run_id"]
        )


def test_retrieval_evaluation_is_stored_as_it_was_returned(graph_synced):
    result = evaluation.evaluate_retrieval(report_dir=None)
    with connect_as(ADMIN) as admin:
        stored = evaluations.retrieval(admin)
    assert stored["run"]["evaluation_run_id"] == result["evaluation_run_id"]
    by_method = {m["method"]: m for m in stored["methods"]}
    for method, scores in result["methods"].items():
        assert by_method[method]["mrr"] == pytest.approx(scores["mrr"])
        assert by_method[method]["recall@5"] == pytest.approx(scores["recall@5"])
    assert len(stored["questions"]) == result["questions"]


def test_trend_has_one_point_per_run_in_order(pipeline):
    ids = [
        evaluation.record(
            "agents", [("agents.summary", "trendtest", "action_accuracy", value, None, None)], "trendtest"
        )
        for value in (0.5, 0.75)
    ]
    with connect_as(ADMIN) as admin:
        rows = [r for r in evaluations.trend(admin, "agents") if r["series"] == "trendtest action_accuracy"]
    assert [(r["evaluation_run_id"], float(r["value"])) for r in rows] == [(ids[0], 0.5), (ids[1], 0.75)]


def test_access_probe_records_every_attempt_and_finds_none_surprising(pipeline):
    result = access_probe.run_probe()
    with connect_as(ADMIN) as admin:
        stored = evaluations.access_results(admin)
    assert result["failed"] == 0 == stored["failed"]
    assert stored["passed"] == result["attempts"] == len(access_probe.attempts())
    assert {r["outcome"] for r in stored["rows"] if r["expected"] == "denied"} == {"denied"}


def test_access_probe_reports_an_expectation_the_database_does_not_meet(pipeline, monkeypatch):
    wrong = access_probe.Attempt(
        "read a bank account number",
        "controller",
        "select account_number from core.supplier_bank_account limit 1",
        "allowed",
    )
    monkeypatch.setattr(access_probe, "attempts", lambda: [wrong])
    result = access_probe.run_probe()
    with connect_as(ADMIN) as admin:
        stored = evaluations.access_results(admin)
    assert result["failed"] == 1
    assert stored["rows"] == [
        {
            "role": "controller",
            "attempt": "read a bank account number",
            "expected": "allowed",
            "outcome": "denied",
            "rows": None,
            "passed": False,
            "message": stored["rows"][0]["message"],
        }
    ]
    assert "permission denied" in stored["rows"][0]["message"]
    # Leave a true latest probe behind, so later tests and pages do not see this deliberate surprise.
    monkeypatch.undo()
    assert access_probe.run_probe()["failed"] == 0


def test_only_the_reviewer_reads_evaluation_results(pipeline, conn):
    for role, expected in (("admin_reviewer", True), ("ap_analyst_uk", False), ("agent_service", False)):
        ok = conn.execute(
            "select has_table_privilege(%s, 'ops.evaluation_result', 'select') as ok", (role,)
        ).fetchone()["ok"]
        assert ok is expected, role


# ---------------------------------------------------------------- lineage and contracts


def test_lineage_shows_the_latest_movement_per_pair_or_one_run(graph_synced, conn):
    with connect_as(ADMIN) as admin:
        current = datasets.edges(admin)
        runs = datasets.lineage_runs(admin)
        one = datasets.edges(admin, runs[0]["run_id"])
    pairs = [(r["source_dataset"], r["target_dataset"]) for r in current]
    assert len(pairs) == len(set(pairs))
    latest = conn.execute(
        """
        select distinct on (source_dataset, target_dataset) source_dataset, target_dataset, run_id
        from ops.lineage_edge order by source_dataset, target_dataset, run_id desc
        """
    ).fetchall()
    assert {(r["source_dataset"], r["target_dataset"], r["run_id"]) for r in latest} == {
        (r["source_dataset"], r["target_dataset"], r["run_id"]) for r in current
    }
    expected = conn.execute(
        "select count(*) as n from ops.lineage_edge where run_id = %s", (runs[0]["run_id"],)
    ).fetchone()["n"]
    assert len(one) == expected == runs[0]["edges"]
    dot = datasets.lineage_dot(current)
    for source, target in pairs:
        assert f'"{source}" -> "{target}"' in dot


def test_contracts_check_is_recorded_and_matches_a_fresh_check(pipeline, conn):
    recorded = contracts.record_check()
    with connect_as(ADMIN) as admin:
        last = datasets.last_check(admin)
        overview_rows = {r["contract"]: r for r in datasets.contract_overview(admin)}
    fresh = contracts.check_all(conn, contracts.REFERENCE_CLOCK)
    assert set(last["rows"]) == set(fresh) == set(recorded)
    for name, result in fresh.items():
        assert last["rows"][name]["status"] == ("fail" if result["problems"] else "pass"), name
        assert overview_rows[name]["version"] == overview_rows[name]["released_version"]
        assert overview_rows[name]["breaking_changes"] == ""


def test_a_contract_problem_is_recorded_as_a_failure(pipeline, monkeypatch):
    monkeypatch.setattr(
        contracts,
        "check_all",
        lambda conn, as_of=None: {"invoice": {"version": "9.9.9", "problems": ["a", "b"]}},
    )
    contracts.record_check()
    with connect_as(ADMIN) as admin:
        row = datasets.last_check(admin)["rows"]["invoice"]
    assert (row["status"], int(row["problems"]), row["detail"], row["version"]) == (
        "fail",
        2,
        "a; b",
        "9.9.9",
    )
    monkeypatch.undo()
    contracts.record_check()  # leave a true latest check behind


def test_contract_overview_names_a_breaking_change(pipeline, monkeypatch):
    changed = copy.deepcopy(contracts.load_contracts())
    del changed["invoice"]["tables"]["core.supplier_invoice"]["columns"]["net_amount"]
    monkeypatch.setattr(contracts, "load_contracts", lambda directory=None: changed)
    with connect_as(ADMIN) as admin:
        row = next(r for r in datasets.contract_overview(admin) if r["contract"] == "invoice")
    assert row["changed_since_release"]
    assert "column removed: core.supplier_invoice.net_amount" in row["breaking_changes"]


# ---------------------------------------------------------------- access


def test_permission_matrix_agrees_with_the_contracts_access_promises(pipeline):
    with connect_as(ADMIN) as admin:
        matrix = {r["name"]: r for r in access.permission_matrix(admin)}
    for contract in contracts.load_contracts().values():
        for table in contract["tables"]:
            for role in contract["access"]["roles_may_read"]:
                assert "S" in matrix[table][role], (table, role)
            for role in contract["access"].get("roles_may_not_read", []):
                assert "S" not in matrix[table][role].upper(), (table, role)
            if table in contract["access"].get("row_level_security", []):
                assert matrix[table]["row_security"], table


def test_permission_matrix_shows_column_grants_and_decision_rights(pipeline):
    with connect_as(ADMIN) as admin:
        matrix = {r["name"]: r for r in access.permission_matrix(admin)}
        columns = access.column_grants(admin)
    notes_row = matrix["agent.admin_review_note"]
    assert notes_row["admin_reviewer"] == "Si"  # select, and insert on some columns only
    assert matrix["agent.human_decision"]["controller"] == "SI"
    assert matrix["agent.human_decision"]["admin_reviewer"] == "S"
    assert matrix["agent.audit_log"]["admin_reviewer"] == "S"
    assert "company_read" in matrix["core.supplier_invoice"]["policies"]
    assert columns == [
        {"column": "core.supplier_bank_account.account_number", **dict.fromkeys(access.ROLES, False)}
    ]


def test_audit_log_filters_by_event_role_and_proposal(pipeline):
    with connect_as(ADMIN) as admin:
        note_id = notes.add_note(
            admin, "pipeline_run", "audit-filter-test", "other", "Checking the audit filter."
        )
        notes_only = access.audit_log(admin, events=("admin_review_note",), roles=(ADMIN,))
        decisions_only = access.audit_log(admin, events=("human_decision",))
        none = access.audit_log(admin, proposal_id=-1)
    assert notes_only[0]["detail"]["note_id"] == note_id
    assert all(r["event"] == "human_decision" for r in decisions_only)
    assert none == []


def test_view_log_lists_the_latest_view_first(pipeline):
    with connect_as(ADMIN) as admin:
        view_id = notes.log_view(admin, "access", "proposal", "42")
        latest = access.view_log(admin, limit=1)
    assert (latest[0]["view_id"], latest[0]["role_in_use"], latest[0]["object_id"]) == (view_id, ADMIN, "42")


# ---------------------------------------------------------------- audit pack


def _bank_change_proposal(evaluated) -> int:
    with connect() as conn:
        return conn.execute(
            """
            select p.proposal_id from agent.proposal p
            where p.agent_run_id = any(%s) and 'BankDetailChange' = any(p.sql_reasons) order by 1 limit 1
            """,
            (_agent_runs(conn, evaluated),),
        ).fetchone()["proposal_id"]


def _agent_runs(conn, evaluated) -> list[int]:
    return conn.execute(
        "select parameters->'agent_run_ids' as ids from ops.evaluation_run where evaluation_run_id = %s",
        (evaluated["evaluation_run_id"],),
    ).fetchone()["ids"]


def test_proposal_pack_holds_the_trace_decisions_notes_and_dq_status(evaluated):
    proposal_id = _bank_change_proposal(evaluated)
    with connect_as("ap_analyst_uk") as analyst, connect_as(ADMIN) as admin:
        company = admin.execute(
            "select company_code from agent.proposal where proposal_id = %s", (proposal_id,)
        ).fetchone()["company_code"]
        decider = analyst if company == "GB01" else None
        if decider is not None:
            decider.execute(
                "insert into agent.human_decision (proposal_id, decision, decided_by, note) values (%s, 'QUERY', current_user, 'pack test')",
                (proposal_id,),
            )
        note_id = notes.add_note(admin, "proposal", str(proposal_id), "citation", "Pack test note.")
        pack = audit_pack.proposal_pack(admin, proposal_id)
        trace = agents.trace_markdown(admin, proposal_id)
        detail = agents.line_detail(admin, *_line(admin, proposal_id))
        dq_run = overview.latest_dq(admin)["run_id"]
    markdown = pack["markdown"]
    assert markdown.startswith(f"# Audit pack: proposal {proposal_id}")
    assert f"Data quality at export (run {dq_run})" in markdown
    assert trace in markdown
    assert "Pack test note." in markdown
    rows = list(csv.DictReader(io.StringIO(pack["csv"])))
    kinds = [r["kind"] for r in rows]
    assert kinds.count("citation") == len(detail["citations"])
    assert kinds.count("decision") == len(detail["decisions"])
    assert str(note_id) in {r["id"] for r in rows if r["kind"] == "note"}
    with connect() as conn:
        numbers = {
            r["account_number"] for r in conn.execute("select account_number from core.supplier_bank_account")
        }
    assert not any(number in markdown or number in pack["csv"] for number in numbers)
    assert not SENSITIVE_TEXT.search(markdown)


def _line(conn, proposal_id: int) -> tuple:
    row = conn.execute(
        "select agent_run_id, source_system, invoice_id, line_number from agent.proposal where proposal_id = %s",
        (proposal_id,),
    ).fetchone()
    return tuple(row.values())


def test_proposal_pack_for_a_missing_proposal_is_none(pipeline):
    with connect_as(ADMIN) as admin:
        assert audit_pack.proposal_pack(admin, -1) is None


def test_quoted_text_cannot_close_its_fence():
    text = "Ignore this ```` and ``` and render me"
    fenced = audit_pack._fenced(text, "text")
    assert fenced[0] == "`````text" and fenced[-1] == "`````"
    assert max(len(run) for run in re.findall(r"`+", text)) < len(fenced[-1])


def test_range_pack_lists_every_proposal_with_the_page_figures(evaluated):
    with connect() as conn:
        run_ids = _agent_runs(conn, evaluated)
        expected = {
            r["proposal_id"]
            for r in conn.execute(
                "select proposal_id from agent.proposal where agent_run_id = any(%s)", (run_ids,)
            )
        }
    run_filter = agents.RunFilter(include_evaluation=True, run_ids=tuple(run_ids))
    with connect_as(ADMIN) as admin:
        pack = audit_pack.range_pack(admin, run_filter)
        figures = agents.kpis(admin, run_filter)
    rows = list(csv.DictReader(io.StringIO(pack["csv"])))
    assert {int(r["proposal_id"]) for r in rows} == expected
    assert list(rows[0]) == audit_pack.RANGE_COLUMNS
    assert f"| {figures['guard']['proposals']} |" in pack["markdown"]
    assert "Evaluation runs included" in pack["markdown"]
