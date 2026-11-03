"""Admin Rules and states, Ontology, and Entities pages: each query module against the planted data or its own source.

The rule registry, the workflow drawing, the guard catalogue and the lifecycle are each compared with what they describe:
the matching SQL, the compiled LangGraph graph, the code that raises flags, and what PostgreSQL accepts when each role
tries each decision. A description that drifts from its source fails here.
"""

import ast
import re

import psycopg
import pytest
from psycopg import sql

from p2p_recon import graph, ontology, review
from p2p_recon.admin import agents, entities, overview, rules
from p2p_recon.admin import ontology as ontology_view
from p2p_recon.admin.masking import SENSITIVE_TEXT
from p2p_recon.agents.guard import FLAGS
from p2p_recon.agents.providers import DecisionRequest, OfflineProvider, get_provider
from p2p_recon.agents.tools import LineKey
from p2p_recon.agents.workflow import AgentRun, agent_connection, open_lines
from p2p_recon.config import CASE_DIR
from p2p_recon.db import connect, connect_as

pytestmark = pytest.mark.integration

ADMIN = "admin_reviewer"
SRC = CASE_DIR / "src" / "p2p_recon"


def planted_reasons(pipeline) -> dict[str, list[str]]:
    found: dict[str, set[str]] = {}
    for row in pipeline.expected:
        if row["document_type"] == "SUPPLIER_PAIR":
            continue
        for code in filter(None, row["expected_reason_codes"].split("|")):
            found.setdefault(code, set()).add(row["scenario_id"])
    return {code: sorted(ids) for code, ids in found.items()}


# ---------------------------------------------------------------- business rules


def test_every_rule_in_the_sql_is_in_the_registry_and_every_entry_has_a_rule():
    assert rules.sql_reason_codes() == set(rules.registry()["rules"])


def test_each_rule_has_a_description_and_exactly_the_scenarios_the_generator_plants(pipeline):
    planted = planted_reasons(pipeline)
    for code, rule in rules.registry()["rules"].items():
        assert rule["description"].strip(), code
        assert rule["scenarios"], code
        assert rule["scenarios"] == planted.get(code), code


def test_rule_parameters_exist_and_cover_everything_the_sql_reads(conn, pipeline):
    in_database = {
        r["parameter_code"] for r in conn.execute("select parameter_code from core.policy_parameter")
    }
    registry = rules.registry()
    listed = {
        p
        for spec in (*registry["rules"].values(), *registry["statuses"].values())
        for p in spec["parameters"]
    }
    assert listed <= in_database
    assert rules.sql_policy_codes() <= listed


def test_rule_tables_exist_and_appear_in_the_sql(conn, pipeline):
    sql_text = rules.RECON_SQL.read_text(encoding="utf-8")
    for code, rule in rules.registry()["rules"].items():
        for table in rule["tables"]:
            assert table in sql_text, f"{code}: {table}"
            assert conn.execute("select to_regclass(%s) as t", (table,)).fetchone()["t"], f"{code}: {table}"


def test_statuses_are_exactly_the_match_status_concepts():
    assert set(rules.registry()["statuses"]) == ontology.concept_codes("MatchStatus")


def test_catalogue_counts_what_fired_in_the_latest_run(pipeline, conn):
    with connect_as(ADMIN) as admin:
        catalogue = {r["reason_code"]: r for r in rules.catalogue(admin)}
    lines = {
        r["reason_code"]: r["n"]
        for r in conn.execute("select reason_code, count(*) as n from recon.current_exception group by 1")
    }
    breaches = {
        r["reason_code"]: r["n"]
        for r in conn.execute(
            "select reason_code, count(*) as n from recon.current_control_breach group by 1"
        )
    }
    for code, row in catalogue.items():
        assert row["lines_held"] == lines.get(code, 0), code
        if row["level"] == "requisition":
            assert row["requisitions_breached"] == breaches.get(code, 0), code
    assert catalogue["PriceVariance"]["kind"] == "Matching exception"
    assert (
        catalogue["SegregationOfDuties"]["definition"] == "The same person requested and approved the spend."
    )


def test_parameters_show_the_values_the_policy_delivered(pipeline):
    with connect_as(ADMIN) as admin:
        rows = rules.parameters(admin, ["price_tolerance_pct_default", "split_window_days"])
    assert [(r["parameter_code"], float(r["parameter_value"]), str(r["valid_from"])) for r in rows] == [
        ("price_tolerance_pct_default", 2.0, "2026-01-01"),
        ("split_window_days", 7.0, "2026-01-01"),
    ]


# ---------------------------------------------------------------- agent workflow


@pytest.fixture(scope="module")
def offline_run(graph_synced):
    """A real offline run, marked as evaluation: two held lines, one clean line, and one line the analyst cannot see."""
    with connect() as conn:
        clean = conn.execute(
            "select source_system, invoice_id, line_number from recon.current_match_result "
            "where company_code = 'GB01' and status_code = 'Matched' order by 2, 3 limit 1"
        ).fetchone()
        hidden = conn.execute(
            "select source_system, invoice_id, line_number from recon.current_match_result "
            "where company_code = 'DE01' order by 2, 3 limit 1"
        ).fetchone()
    with agent_connection() as conn, graph.driver() as db:
        run = AgentRun(conn, db, get_provider("offline"), "ap_analyst_uk", {"evaluation": "admin-rules"})
        held = open_lines(conn, "ap_analyst_uk", 2)
        lines = [*held, LineKey(**clean), LineKey(**hidden)]
        outcomes = [run.investigate(line) for line in lines]
        run.finish()
        compiled = run.graph.get_graph()
    return {
        "run_id": run.run_id,
        "held": held,
        "clean": LineKey(**clean),
        "hidden": LineKey(**hidden),
        "outcomes": outcomes,
        "edges": {(e.source, e.target, e.conditional) for e in compiled.edges},
        "nodes": set(compiled.nodes),
    }


def _drawn_edges(dot: str) -> set[tuple[str, str]]:
    return {
        (a, b)
        for a, b in re.findall(r'^\s*"([^"]+)" -> "([^"]+)"', dot, re.MULTILINE)
        if not b.startswith("note_")
    }


def test_drawing_is_the_compiled_graph_of_a_real_run(offline_run):
    assert set(rules.workflow_edges()) == offline_run["edges"]
    assert _drawn_edges(rules.workflow_dot()) == {(a, b) for a, b, _ in offline_run["edges"]}
    drawn_nodes = set(re.findall(r'^\s*"([^"]+)" \[', rules.workflow_dot(), re.MULTILINE)) | {
        n for edge in _drawn_edges(rules.workflow_dot()) for n in edge
    }
    assert {n for n in drawn_nodes if not n.startswith("note_")} == offline_run["nodes"]
    assert set(rules.BRANCHES) <= {(a, b) for a, b, _ in offline_run["edges"]}


def test_run_paths_follow_the_graph_and_tell_the_routes_apart(offline_run):
    with connect_as(ADMIN) as admin:
        paths = rules.run_paths(admin, offline_run["run_id"])
    allowed = {(a, b) for a, b, _ in offline_run["edges"]}
    assert set(paths["edges"]) <= allowed
    by_line = {(p["source_system"], p["invoice_id"], p["line_number"]): p for p in paths["lines"]}
    clean, hidden = offline_run["clean"], offline_run["hidden"]
    assert by_line[(clean.source_system, clean.invoice_id, clean.line_number)]["path"] == (
        "supervisor → controls → matching → auto_draft → submit"
    )
    assert by_line[(hidden.source_system, hidden.invoice_id, hidden.line_number)]["path"] == "supervisor"
    for line in offline_run["held"]:
        assert "investigator" in by_line[(line.source_system, line.invoice_id, line.line_number)]["path"]
    assert all(p["finished"] for p in paths["lines"])
    assert paths["edges"][("supervisor", "__end__")] == 1
    assert paths["edges"][("__start__", "supervisor")] == len(offline_run["outcomes"])


# ---------------------------------------------------------------- guard


def _raised_flags() -> set[str]:
    """Flag names the guard and the workflow can store, read from their source."""
    found = set()
    for name in ("guard.py", "workflow.py"):
        tree = ast.parse((SRC / "agents" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # flags.append("x") / flags.append(f"x:...")
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "append"
                and getattr(node.func.value, "id", None) == "flags"
            ):
                found.add(_flag_name(node.args[0]))
            # "forced_flags": ["x"] / [f"x:..."]
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "forced_flags"
                        and isinstance(value, ast.List)
                    ):
                        found |= {_flag_name(item) for item in value.elts}
    return found


def _flag_name(node: ast.expr) -> str:
    # A plain string, or an f-string, whose literal prefix is the flag name.
    text = node.value if isinstance(node, ast.Constant) else node.values[0].value
    return text.split(":", 1)[0]


def test_every_flag_raised_is_described_and_every_description_is_raised():
    assert _raised_flags() == set(FLAGS)


def test_guard_checks_count_each_flag_and_name_unknown_ones(pipeline, conn):
    line = conn.execute(
        "select run_id, source_system, invoice_id, line_number from recon.current_match_result limit 2"
    ).fetchall()
    run_id = conn.execute(
        "insert into agent.run (provider, model, acting_user, recon_run_id, parameters, status) "
        "values ('offline', 'rules', 'controller', %s, '{\"evaluation\": \"guard-figures\"}', 'succeeded') "
        "returning agent_run_id",
        (line[0]["run_id"],),
    ).fetchone()["agent_run_id"]
    for row, flags in zip(line, (["uncited", "unknown_tool:x"], ["uncited", "made_up"]), strict=True):
        conn.execute(
            "insert into agent.proposal (agent_run_id, source_system, invoice_id, line_number, company_code, sql_status, "
            "sql_reasons, drafted_action, proposed_action, explanation, guard_flags) "
            "values (%s, %s, %s, %s, 'GB01', 'Matched', '{}', null, 'NEEDS_HUMAN_REVIEW', 'test', %s)",
            (run_id, row["source_system"], row["invoice_id"], row["line_number"], flags),
        )
    result = rules.guard_checks(conn, agents.RunFilter(include_evaluation=True, run_ids=(run_id,)))
    fired = {c["flag"]: c["fired"] for c in result["checks"]}
    assert fired["uncited"] == 2 and fired["unknown_tool"] == 1 and fired["auto_approve_blocked"] == 0
    assert result["unknown"] == ["made_up"]


# ---------------------------------------------------------------- action mapping


def test_action_mapping_is_the_offline_providers_and_matches_the_evaluation():
    rows = rules.action_mapping()
    assert [r["when"] for r in rows[: len(OfflineProvider.RULES)]] == [
        f"reason {c}" for c in OfflineProvider.RULES
    ]
    assert all(r["agrees"] for r in rows if r["agrees"] is not None)
    assert rows[-1]["offline_action"] == "NEEDS_HUMAN_REVIEW"


@pytest.mark.parametrize(
    "row", [r for r in rules.action_mapping() if r["agrees"] is not None], ids=lambda r: r["when"]
)
def test_the_offline_provider_proposes_what_the_mapping_shows(row):
    """The table is read from the provider; this checks the provider also behaves as the table says."""
    kind, code = row["when"].split(" ", 2)[:2]
    case = {
        "reasons": [code] if kind == "reason" else [],
        "status": code.rstrip(",") if kind == "status" else "Exception",
        "exceptions": [],
        "evidence_ids": ["row:recon.match_result|{}"],
        "line": {},
    }
    hit = {"citation": "chunk:x", "context_header": "policy", "content": "text"}
    observations = [{"query": row["searches_for"], "hits": [hit]}]
    decision = OfflineProvider().decide(DecisionRequest(case, observations, 2, 4))
    assert decision.data["action"] == row["offline_action"]


# ---------------------------------------------------------------- decisions and the lifecycle


def test_policy_is_parsed_from_the_database_and_matches_the_review_module(pipeline):
    with connect_as(ADMIN) as admin:
        policy = rules.decision_policy(admin)
    assert policy["privileged_roles"] == ["controller"]
    assert set(policy["control_reasons"]) == review.CONTROL_REASONS


def _insert_proposal(conn, run_id: int, invoice: str, company: str, reasons: list[str], action: str) -> int:
    return conn.execute(
        "insert into agent.proposal (agent_run_id, source_system, invoice_id, line_number, company_code, "
        "sql_status, sql_reasons, drafted_action, proposed_action, explanation) "
        "values (%s, 'SAP_LIKE', %s, 1, %s, 'Exception', %s, %s, %s, 'probe') returning proposal_id",
        (run_id, invoice, company, reasons, action, action),
    ).fetchone()["proposal_id"]


def _probe_proposals(conn) -> dict:
    """For each company and kind, a proposal in every lifecycle state. Live runs, inserted as the pipeline login; the
    test's transaction rolls them back."""
    recon_run = conn.execute("select run_id from recon.latest_run").fetchone()["run_id"]
    first, later = (
        conn.execute(
            "insert into agent.run (provider, model, acting_user, recon_run_id, status) "
            "values ('offline', 'rules', 'controller', %s, 'succeeded') returning agent_run_id",
            (recon_run,),
        ).fetchone()["agent_run_id"]
        for _ in range(2)
    )
    probes = {}
    for company in ("GB01", "DE01"):
        for failed_control in (False, True):
            reasons = ["SegregationOfDuties"] if failed_control else ["PriceVariance"]
            for state in (*rules.UNDECIDED, *rules.DECISIONS.values()):
                invoice = f"PROBE-{company}-{failed_control}-{state}"
                action = "AUTO_APPROVE" if state == "Auto-approved" else "CHECK_RECEIPT"
                proposal = _insert_proposal(conn, first, invoice, company, reasons, action)
                if state == "Superseded":
                    _insert_proposal(conn, later, invoice, company, reasons, "CHECK_RECEIPT")
                decision = next((d for d, s in rules.DECISIONS.items() if s == state), None)
                if decision:
                    conn.execute(
                        "insert into agent.human_decision (proposal_id, decision, decided_by, note) values (%s, %s, 'controller', 'prior')",
                        (proposal, decision),
                    )
                probes[(company, failed_control, state)] = proposal
    return probes


def _attempt(conn, role: str, proposal_id: int, decision: str) -> bool:
    try:
        with conn.transaction(force_rollback=True):
            conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
            conn.execute(
                "insert into agent.human_decision (proposal_id, decision, decided_by, note) values (%s, %s, current_user, 'probe')",
                (proposal_id, decision),
            )
        return True
    except psycopg.Error:
        return False


def test_decision_rights_and_lifecycle_are_what_the_database_allows(pipeline, conn):
    """Every role tries every decision on every state of every kind of proposal. What PostgreSQL accepts must be exactly
    what the rights table says, and the decisions drawn must be exactly those some role can make."""
    with connect_as(ADMIN) as admin:
        rights = rules.decision_rights(admin)
    probes = _probe_proposals(conn)
    possible = set()
    for (company, failed_control, state), proposal in probes.items():
        for row in rights:
            for decision in rules.DECISIONS:
                allowed = _attempt(conn, row["role"], proposal, decision)
                assert allowed == rules.may_decide(rights, row["role"], company, failed_control), (
                    row["role"],
                    company,
                    failed_control,
                    state,
                    decision,
                )
                if allowed:
                    possible.add((state, rules.DECISIONS[decision]))
    drawn = {(t["from"], t["to"]) for t in rules.lifecycle_transitions() if t["decision"]}
    assert drawn == possible
    assert {r["role"] for r in rights if r["ordinary_proposals"]} == {
        "ap_analyst_uk",
        "ap_analyst_de",
        "controller",
    }
    assert {r["role"] for r in rights if r["failed_control_proposals"]} == {"controller"}


def test_store_transitions_are_what_the_review_queue_shows(pipeline, conn):
    """Awaiting review is in the queue, auto-approved never is, a later live proposal supersedes, any decision removes."""
    probes = _probe_proposals(conn)

    def queued() -> set[int]:
        with conn.transaction(force_rollback=True):
            conn.execute("set local role controller")
            return {r["proposal_id"] for r in conn.execute("select proposal_id from agent.review_queue")}

    in_queue = queued()
    for (_, _, state), proposal in probes.items():
        assert (proposal in in_queue) == (state == "Awaiting review"), state
    awaiting = probes[("GB01", False, "Awaiting review")]
    conn.execute(
        "insert into agent.human_decision (proposal_id, decision, decided_by, note) values (%s, 'QUERY', 'controller', 'q')",
        (awaiting,),
    )
    assert awaiting not in queued()
    drawn = {(a, b) for a, b, _ in rules.STORE_TRANSITIONS}
    assert drawn == {
        ("Stored", "Awaiting review"),
        ("Stored", "Auto-approved"),
        ("Awaiting review", "Superseded"),
    }


def test_lifecycle_drawing_shows_every_transition_listed():
    dot = rules.lifecycle_dot()
    for source, target, _ in rules.STORE_TRANSITIONS:
        assert f'"{source}" -> "{target}"' in dot
    for state in (*rules.UNDECIDED, *rules.DECISIONS.values()):
        assert f'"{state}" -> "decision"' in dot
    for decision, state in rules.DECISIONS.items():
        assert f'"decision" -> "{state}" [label="{decision}"]' in dot


# ---------------------------------------------------------------- ontology


@pytest.fixture(scope="module")
def recorded_validation(pipeline):
    """The sample is replaced by two known chains: S01, clean, and S12, which breaks segregation of duties."""
    chosen = [
        next(r for r in pipeline.expected if r["scenario_id"] == s and r["document_type"] == "INVOICE_LINE")
        for s in ("S01", "S12")
    ]
    sample = [{"source_system": r["source_system"], "invoice_id": r["document_number"]} for r in chosen]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ontology, "sample_invoices", lambda conn, size: sample)
        counts = ontology.record_validation()
    return {"counts": counts, "clean": sample[0], "breach": sample[1]}


def _chain(row: dict) -> str:
    return f"shacl.chain.{row['source_system']}:{row['invoice_id']}"


def test_validation_is_recorded_and_matches_a_fresh_validation(recorded_validation, conn):
    assert recorded_validation["counts"] == {"checks": 4, "passed": 3, "chains": 2}
    with connect_as(ADMIN) as admin:
        stored = {r["check_name"]: r for r in ontology_view.latest_validation(admin)["rows"]}
        status = overview.ontology_status(admin)
    assert stored["shacl.example.valid-chain"]["status"] == "pass"
    assert stored["shacl.example.invalid-chain"]["status"] == "pass"  # it does not conform, as designed
    clean, breach = (
        stored[_chain(recorded_validation["clean"])],
        stored[_chain(recorded_validation["breach"])],
    )
    assert (clean["status"], int(clean["violations"]), clean["detail"]) == ("pass", 0, None)
    assert breach["status"] == "fail" and "Segregation of duties" in breach["detail"]
    assert "SegregationOfDuties" in breach["sql_reasons"]  # SQL holds what SHACL rejects
    assert (status["checked"], status["conforming"]) == (2, 1)
    exported = ontology.export_rdf(conn)
    for row in (recorded_validation["clean"], recorded_validation["breach"]):
        fresh = ontology.validate_graph(
            ontology.document_closure(exported, ontology.invoice_chain_nodes(conn, **row))
        )
        assert int(stored[_chain(row)]["violations"]) == len(fresh.messages)


def test_coverage_matrix_counts_rows_and_nodes_and_names_unmapped_classes(graph_synced, conn):
    with connect_as(ADMIN) as admin:
        cover = {r["class"]: r for r in ontology_view.coverage(admin)["rows"]}
    mapped = ontology.load_mapping()["classes"]
    for name in mapped:
        expected = len(ontology.class_rows(conn, name))
        assert cover[name]["rows"] == expected == cover[name]["nodes"], name
        assert cover[name]["gap"] is None, name
    unmapped = {name for name, row in cover.items() if row["table"] is None}
    assert unmapped == {c["class"] for c in ontology_view.classes()} - set(mapped)
    assert {"Party", "PaymentProposal", "JournalEntry"} <= unmapped
    assert all(cover[name]["gap"].startswith("not mapped") for name in unmapped)


def test_drift_check_finds_a_code_the_vocabulary_lacks(pipeline, conn):
    baseline = ontology_view.drift(conn)
    assert baseline["used_not_in_vocabulary"] == []
    assert baseline["in_sql_not_in_vocabulary"] == []
    assert baseline["not_used_in_latest_run"] == []  # the generator plants every status and reason
    assert "InvoiceBeforeReceipt" in {c["concept"] for c in baseline["missing_definition"]}
    assert "PriceVariance" not in {c["concept"] for c in baseline["missing_definition"]}
    line = conn.execute(
        "select run_id, source_system, invoice_id, line_number from recon.current_match_result limit 1"
    ).fetchone()
    conn.execute(
        "insert into recon.exception (run_id, source_system, invoice_id, line_number, reason_code, evidence) "
        "values (%s, %s, %s, %s, 'MadeUpReason', '{}')",
        tuple(line.values()),
    )
    drifted = ontology_view.drift(conn)
    assert drifted["used_not_in_vocabulary"] == [{"code": "MadeUpReason", "column": "reason_code", "rows": 1}]


def test_concepts_carry_english_and_german_labels():
    by_name = {c["concept"]: c for c in ontology_view.concepts()}
    assert by_name["PO"]["label_en"] == "Purchase order"
    assert by_name["PO"]["also_de"] == "Bestellung"
    assert by_name["PriceVariance"]["broader"] == "MatchingException"


def test_shapes_include_the_sparql_constraints_as_written():
    sparql = {r["message"]: r["sparql"] for r in ontology_view.shapes() if r["constraint"] == "SPARQL"}
    assert set(sparql) == {
        "Segregation of duties: the requester approved their own requisition.",
        "Approval limit exceeded: requisition amount is above the approver's limit.",
        "Exception reason must be a concept in the ExceptionReason scheme.",
    }
    assert all(text.startswith("SELECT $this") for text in sparql.values())


def test_change_impact_lists_what_depends_on_a_term():
    requisition = ontology_view.impact("class", "PurchaseRequisition")
    assert "core.purchase_requisition" in requisition["tables"]
    assert "(PurchaseRequisition)-[:REQUESTED_BY]->(Employee)" in requisition["relationship_types"]
    assert "PurchaseRequisitionShape" in requisition["shapes"]
    assert {"rule": "SegregationOfDuties", "scenarios": ["S12"]} in requisition["rules_reading_these_tables"]

    requested_by = ontology_view.impact("property", "requestedBy")
    assert "core.purchase_requisition.requester_id" in requested_by["columns"]

    bank = ontology_view.impact("concept", "BankDetailChange")
    assert bank["offline_action"] == "VERIFY_BANK_DETAILS"
    assert bank["columns"] == ["recon.exception.reason_code"]
    assert bank["rules_reading_these_tables"] == [{"rule": "BankDetailChange", "scenarios": ["S14"]}]

    assert "MatchResultShape" in ontology_view.impact("concept", "Matched")["shapes"]


def test_every_term_has_an_impact_view():
    for kind, name in ontology_view.terms():
        assert isinstance(ontology_view.impact(kind, name), dict), name


# ---------------------------------------------------------------- entities


def _s01_invoice(pipeline) -> dict:
    return next(
        r for r in pipeline.expected if r["scenario_id"] == "S01" and r["document_type"] == "INVOICE_LINE"
    )


def test_search_returns_the_graph_key_and_treats_text_as_data(pipeline):
    row = _s01_invoice(pipeline)
    with connect_as(ADMIN) as admin:
        found = entities.search(admin, "invoice", row["document_number"])
        assert f"{row['source_system']}|{row['document_number']}" in [f["key"] for f in found]
        assert entities.search(admin, "invoice", "'; drop table core.supplier_invoice; --") == []


def test_neighbourhood_matches_postgres(graph_synced, pipeline, conn):
    row = _s01_invoice(pipeline)
    key = f"{row['source_system']}|{row['document_number']}"
    with connect_as(ADMIN) as admin:
        scope = entities.reviewer_scope(admin)
    with graph.driver() as db:
        around = entities.neighbourhood(db, scope, "SupplierInvoice", key, 1)
        typed = entities.neighbourhood(db, scope, "SupplierInvoice", key, 2, keep_types=["HAS_LINE"])
    lines = conn.execute(
        "select count(*) as n from core.invoice_line where source_system = %s and invoice_id = %s and not is_deleted",
        (row["source_system"], row["document_number"]),
    ).fetchone()["n"]
    types = [t for _, _, t in around["edges"]]
    assert types.count("HAS_LINE") == lines
    assert {"ISSUED_BY", "BELONGS_TO"} <= set(types)
    assert {t for _, _, t in typed["edges"]} == {"HAS_LINE"}
    assert around["nodes"][around["start"]]["key"] == key


def test_neighbourhood_is_scoped_in_the_query(graph_synced, conn):
    gid = conn.execute(
        """
        select supplier_gid from mdm.supplier_xref group by 1
        having count(distinct source_system) = 2 order by 1 limit 1
        """
    ).fetchone()["supplier_gid"]
    with graph.driver() as db:
        both = entities.neighbourhood(
            db, entities.Scope(["GB01", "DE01"], ["SAP_LIKE", "ORACLE_LIKE"]), "GoldenSupplier", gid
        )
        uk = entities.neighbourhood(db, entities.Scope(["GB01"], ["SAP_LIKE"]), "GoldenSupplier", gid)
        de_start = conn.execute(
            "select source_supplier_id from core.supplier where source_system = 'ORACLE_LIKE' limit 1"
        ).fetchone()
        hidden = entities.neighbourhood(
            db,
            entities.Scope(["GB01"], ["SAP_LIKE"]),
            "Supplier",
            f"ORACLE_LIKE|{de_start['source_supplier_id']}",
        )
    systems = lambda around: {n["properties"].get("sourceSystem") for n in around["nodes"].values()} - {None}  # noqa: E731
    assert systems(both) == {"SAP_LIKE", "ORACLE_LIKE"}
    assert systems(uk) == {"SAP_LIKE"}
    assert not hidden["found"]


def test_neighbourhood_refuses_unknown_labels_and_depths(graph_synced):
    scope = entities.Scope(["GB01"], ["SAP_LIKE"])
    with graph.driver() as db:
        with pytest.raises(ValueError):
            entities.neighbourhood(db, scope, "Supplier) DETACH DELETE (n", "x")
        with pytest.raises(ValueError):
            entities.neighbourhood(db, scope, "Supplier", "x", depth=4)


def test_no_bank_number_reaches_a_node_property(graph_synced, conn):
    supplier = conn.execute(
        "select source_system, source_supplier_id from core.supplier_bank_account group by 1, 2 having count(*) > 1 limit 1"
    ).fetchone()
    with graph.driver() as db:
        around = entities.neighbourhood(
            db,
            entities.Scope(["GB01", "DE01"], ["SAP_LIKE", "ORACLE_LIKE"]),
            "Supplier",
            f"{supplier['source_system']}|{supplier['source_supplier_id']}",
        )
    assert any(n["label"] == "BankAccount" for n in around["nodes"].values())
    numbers = {
        r["account_number"] for r in conn.execute("select account_number from core.supplier_bank_account")
    }
    for node in around["nodes"].values():
        for value in node["properties"].values():
            assert str(value) not in numbers and not SENSITIVE_TEXT.search(str(value))


def test_chain_gaps_are_limited_to_the_scope(graph_synced):
    with graph.driver() as db:
        everything = graph.run_query("chain_gaps")
        full = entities.chain_gaps(db, entities.Scope(["GB01", "DE01"], ["SAP_LIKE", "ORACLE_LIKE"]))
        uk = entities.chain_gaps(db, entities.Scope(["GB01"], ["SAP_LIKE"]))
        none = entities.chain_gaps(db, entities.Scope([], []))
    assert len(full["rows"]) == len(everything)
    assert len(uk["rows"]) == sum(1 for r in everything if r["source_system"] == "SAP_LIKE")
    assert none["rows"] == [] and none["counts"] == []


def test_supplier_resolution_shows_members_pairs_and_the_steward_queue(pipeline, conn):
    with connect_as(ADMIN) as admin:
        golden = entities.golden_suppliers(admin)
        biggest = golden[0]
        detail = entities.golden_detail(admin, biggest["supplier_gid"])
        queue = entities.steward_queue(admin)
    assert biggest["member_count"] == len(detail["members"]) > 1
    assert all(p["evidence"] for p in detail["pairs"])
    expected = conn.execute(
        "select count(*) as n from mdm.supplier_match_candidate where decision = 'STEWARD_REVIEW'"
    ).fetchone()["n"]
    assert len(queue) == expected > 0
