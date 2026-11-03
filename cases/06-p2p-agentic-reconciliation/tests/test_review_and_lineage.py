"""A proposal traces back to source rows and documents, and the review page behaves for each role."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from p2p_recon import graph, lineage, review
from p2p_recon.agents.providers import OfflineProvider
from p2p_recon.agents.tools import LineKey
from p2p_recon.agents.workflow import AgentRun, agent_connection
from p2p_recon.db import connect_as
from p2p_recon.retrieval import load_user

pytestmark = pytest.mark.integration

APP = str(Path(__file__).parents[1] / "src" / "p2p_recon" / "review_app.py")
BANK_CHANGE = LineKey("SAP_LIKE", "5100000009", 1)  # S14
LATE_CORRECTION = LineKey("ORACLE_LIKE", "90010", 1)  # S17: the receipt was corrected in batch 2
CONTROL = LineKey("SAP_LIKE", "5100000007", 1)  # S11
CROSS_ERP = LineKey("ORACLE_LIKE", "90012", 1)  # S21


@pytest.fixture(scope="module")
def db(graph_synced):
    with graph.driver() as driver:
        yield driver


def propose(db, key, user):
    with agent_connection() as conn:
        run = AgentRun(conn, db, OfflineProvider(), user)
        outcome = run.investigate(key)
        run.finish()
    return outcome.proposal_id


# ---------------------------------------------------------------- lineage


@pytest.fixture(scope="module")
def bank_trace(db):
    return lineage.trace_proposal(propose(db, BANK_CHANGE, "ap_analyst_uk"))


def test_trace_reaches_every_layer(bank_trace):
    assert bank_trace["proposal"]["sql_reasons"] == ["BankDetailChange"]
    assert bank_trace["run"]["acting_user"] == "ap_analyst_uk"
    assert {s["agent_name"] for s in bank_trace["steps"]} >= {
        "supervisor",
        "controls",
        "matching",
        "investigator",
        "guard",
    }
    assert {c["tool_name"] for c in bank_trace["tool_calls"]} >= {"search_documents", "get_document_chain"}
    assert all(row["row"] is not None for row in bank_trace["cited_rows"])
    assert not any(chunk.get("missing") for chunk in bank_trace["cited_chunks"])
    assert [event["event"] for event in bank_trace["audit"]] == ["proposal_stored"]


def test_trace_follows_the_invoice_to_its_delivered_files(bank_trace):
    by_table = {}
    for item in bank_trace["source_chain"]:
        by_table.setdefault(item["table"], []).append(item)
    assert {
        "core.invoice_line",
        "core.supplier_invoice",
        "core.po_line",
        "core.purchase_order",
        "core.grn_line",
        "core.purchase_requisition",
        "core.approval",
        "core.supplier",
    } <= set(by_table)
    origin = by_table["core.invoice_line"][0]["origin"]
    assert origin["raw_table"] == "raw.sap_invoice_item"
    assert origin["source_file"].endswith("sap_invoice_item.csv")
    assert origin["file_row"] > 0
    assert len(origin["checksum"]) == 64
    assert all(item["origin"] is not None for item in bank_trace["source_chain"])


def test_trace_cites_the_documents_with_their_file_and_checksum(bank_trace):
    chunks = {c["chunk_id"]: c for c in bank_trace["cited_chunks"]}
    rule = chunks["policy_p2p:bank-change-window-days"]
    assert rule["file_name"] == "policy_p2p.md"
    assert len(rule["content_sha256"]) == 64


def test_trace_shows_the_corrected_row_after_a_late_delivery(db):
    """S17: the core row now rests on batch 2, and the trace has to say so rather than pointing at the superseded row."""
    trace = lineage.trace_proposal(propose(db, LATE_CORRECTION, "ap_analyst_de"))
    receipts = [item for item in trace["source_chain"] if item["table"] == "core.grn_line"]
    assert receipts
    assert "batch_002" in {item["origin"]["batch_id"] for item in receipts}


def test_dataset_edges_connect_every_layer(pipeline, loaded_documents, conn):
    from p2p_recon.db import pipeline_run

    with pipeline_run("agent_run_test") as run:
        run.lineage(conn, "recon.match_result", "agent.proposal", 1)
        conn.commit()
    edges = {
        (e["source_dataset"], e["target_dataset"]) for e in lineage.dataset_path(conn, ["agent.proposal"])
    }
    assert ("recon.match_result", "agent.proposal") in edges
    assert ("core.invoice_line", "recon.match_result") in edges
    assert ("stg.invoice_line", "core.invoice_line") in edges
    assert ("raw.sap_invoice_item", "stg.invoice_line") in edges
    assert any(
        source.startswith("file:")
        and source.endswith("sap_invoice_item.csv")
        and target == "raw.sap_invoice_item"
        for source, target in edges
    )


def test_markdown_holds_no_bank_numbers_or_raw_values(bank_trace, conn):
    text = lineage.render_markdown(bank_trace)
    assert "5100000009" in text
    numbers = [
        r["account_number"] for r in conn.execute("select account_number from core.supplier_bank_account")
    ]
    assert not any(number in text for number in numbers)


def test_unknown_proposal_is_refused(pipeline):
    with pytest.raises(ValueError, match="No proposal"):
        lineage.trace_proposal(-1)


# ---------------------------------------------------------------- review functions


def test_queue_is_filtered_by_the_persons_company(db):
    proposal = propose(db, BANK_CHANGE, "ap_analyst_uk")
    with connect_as("ap_analyst_uk") as uk, connect_as("ap_analyst_de") as de:
        assert proposal in [line["proposal_id"] for line in review.queue(uk)]
        assert proposal not in [line["proposal_id"] for line in review.queue(de)]
        assert review.proposal_detail(de, proposal) is None
        detail = review.proposal_detail(uk, proposal)
    assert detail["match"]["status_code"] == "Exception"
    documents = [c for c in detail["citations"] if c["kind"] == "document"]
    assert documents
    assert all(c["content"] for c in documents)
    assert all(c["row"] for c in detail["citations"] if c["kind"] == "row")


def test_a_citation_the_reader_cannot_see_says_so(db, conn):
    """An auditor's proposal can cite an audit memo. An analyst reading it sees that a citation exists, and not its text."""
    proposal = propose(db, BANK_CHANGE, "ap_analyst_uk")
    conn.rollback()
    conn.execute(
        "insert into agent.citation (proposal_id, kind, chunk_id) values (%s, 'chunk', 'audit_memo_GB01:planned-control-testing')",
        (proposal,),
    )
    conn.commit()
    with connect_as("ap_analyst_uk") as analyst, connect_as("auditor") as auditor:
        hidden = next(
            c for c in review.citation_details(analyst, proposal) if c["reference"].startswith("audit_memo")
        )
        shown = next(
            c for c in review.citation_details(auditor, proposal) if c["reference"].startswith("audit_memo")
        )
    assert hidden["content"] is None
    assert shown["content"]


def test_graph_path_is_a_valid_picture_of_the_chain(db):
    with connect_as("ap_analyst_uk") as conn:
        user = load_user(conn, "ap_analyst_uk")
    dot = review.graph_path_dot(user, db, BANK_CHANGE)
    assert dot.startswith("digraph path {")
    assert dot.rstrip().endswith("}")
    for expected in ("Invoice", "Supplier", "PO", "Receipt", "Requisition", "Approver"):
        assert expected in dot
    assert review.graph_path_dot(user, db, CROSS_ERP) is None


def test_graph_path_marks_what_another_company_holds_without_naming_it(db):
    with connect_as("ap_analyst_de") as conn:
        user = load_user(conn, "ap_analyst_de")
    dot = review.graph_path_dot(user, db, CROSS_ERP)
    assert "1 record(s)" in dot
    assert "0000100001" not in dot


# ---------------------------------------------------------------- the page


def page(role, proposal=None):
    app = AppTest.from_file(APP, default_timeout=60)
    app.query_params["user"] = role
    if proposal:
        app.query_params["proposal"] = str(proposal)
    return app.run()


def button_labels(app):
    return [button.label for button in app.button]


def test_page_shows_the_queue_explanation_citations_and_graph(db):
    proposal = propose(db, BANK_CHANGE, "ap_analyst_uk")
    app = page("ap_analyst_uk", proposal)
    assert not app.exception
    text = " ".join(m.value for m in app.markdown)
    assert "VERIFY_BANK_DETAILS" in text
    assert "explanation" in text
    labels = [e.label for e in app.expander]
    assert any(label.startswith("Document: policy_p2p:") for label in labels)
    assert any(label.startswith("Row: recon.match_result") for label in labels)
    assert len(app.dataframe) == 1
    assert app.get("graphviz_chart")


def test_accept_is_recorded_and_leaves_the_queue(db, conn):
    proposal = propose(db, BANK_CHANGE, "ap_analyst_uk")
    app = page("ap_analyst_uk", proposal)
    next(b for b in app.button if b.label == "Accept proposal").click().run()
    assert not app.exception
    assert any("Nothing has been posted or paid" in s.value for s in app.success)
    conn.rollback()
    decision = conn.execute(
        "select decision, decided_by from agent.human_decision where proposal_id = %s", (proposal,)
    ).fetchone()
    assert (decision["decision"], decision["decided_by"]) == ("ACCEPT", "ap_analyst_uk")
    assert (
        conn.execute(
            "select count(*) as n from agent.review_queue where proposal_id = %s", (proposal,)
        ).fetchone()["n"]
        == 0
    )


def test_override_without_a_reason_is_refused_on_the_page(db, conn):
    proposal = propose(db, BANK_CHANGE, "ap_analyst_uk")
    app = page("ap_analyst_uk", proposal)
    next(b for b in app.button if b.label == "Override").click().run()
    assert any("must say why" in e.value for e in app.error)
    conn.rollback()
    assert (
        conn.execute(
            "select count(*) as n from agent.human_decision where proposal_id = %s", (proposal,)
        ).fetchone()["n"]
        == 0
    )


def test_override_with_a_reason_is_recorded(db, conn):
    proposal = propose(db, BANK_CHANGE, "ap_analyst_uk")
    app = page("ap_analyst_uk", proposal)
    app.text_area[0].set_value("Verified by telephone").run()
    next(b for b in app.button if b.label == "Override").click().run()
    assert app.success
    conn.rollback()
    decision = conn.execute(
        "select decision, note from agent.human_decision where proposal_id = %s", (proposal,)
    ).fetchone()
    assert (decision["decision"], decision["note"]) == ("OVERRIDE", "Verified by telephone")


def test_auditor_gets_no_decision_buttons(db):
    proposal = propose(db, BANK_CHANGE, "ap_analyst_uk")
    app = page("auditor", proposal)
    assert not app.exception
    assert button_labels(app) == []
    assert any("read-only" in i.value for i in app.info)


def test_analyst_gets_no_decision_buttons_on_a_failed_control(db):
    proposal = propose(db, CONTROL, "ap_analyst_uk")
    app = page("ap_analyst_uk", proposal)
    assert button_labels(app) == []
    assert any("controller" in i.value for i in app.info)
    assert "Accept proposal" in button_labels(page("controller", proposal))


def test_other_companys_analyst_does_not_see_the_proposal(db):
    proposal = propose(db, BANK_CHANGE, "ap_analyst_uk")
    app = page("ap_analyst_de", proposal)
    assert not app.exception
    assert str(proposal) not in [str(option) for box in app.selectbox for option in box.options]
