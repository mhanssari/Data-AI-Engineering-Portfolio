"""The Neo4j projection reconciles to PostgreSQL, and the Cypher library answers correctly."""

import pytest

from p2p_recon import graph

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def synced(pipeline):
    try:
        with graph.driver() as db:
            db.verify_connectivity()
    except Exception as exc:
        pytest.fail(
            f"Neo4j is not reachable. Start it: docker compose -f infra/docker-compose.yml --env-file .env up -d ({exc})"
        )
    return graph.sync_graph()


def _scenario(pipeline, scenario, status=None):
    return next(
        r
        for r in pipeline.expected
        if r["scenario_id"] == scenario
        and r["document_type"] == "INVOICE_LINE"
        and (status is None or r["expected_status"] == status)
    )


def test_counts_reconcile_to_postgres(synced):
    assert synced["nodes"] > 1000
    assert synced["edges"] > 1000
    assert synced["reconciliation_failures"] == []


def test_rebuild_is_idempotent(synced):
    assert graph.sync_graph() == synced


def test_bank_account_number_is_absent_from_the_graph(synced):
    with graph.driver() as db, db.session() as session:
        keys = session.run(
            "MATCH (n:BankAccount) UNWIND keys(n) AS k RETURN collect(DISTINCT k) AS keys"
        ).single()["keys"]
    assert "account_number" not in keys and "accountNumber" not in keys


def test_invoice_chain_returns_the_whole_chain(pipeline, synced):
    row = _scenario(pipeline, "S01")
    result = graph.run_query(
        "invoice_chain", source_system=row["source_system"], invoice_id=row["document_number"]
    )
    assert len(result) == 1
    chain = result[0]
    assert chain["po_number"] and chain["grn_numbers"] and chain["pr_numbers"] and chain["approvers"]
    assert chain["golden_supplier"]
    assert chain["match_status"] == "Matched"
    assert chain["reasons"] == []


def test_invoice_chain_shows_the_exception_reason(pipeline, synced):
    row = _scenario(pipeline, "S03")
    result = graph.run_query(
        "invoice_chain", source_system=row["source_system"], invoice_id=row["document_number"]
    )
    assert result[0]["match_status"] == "Exception"
    assert result[0]["reasons"] == ["PriceVariance"]


def test_chain_gaps_agree_with_sql(pipeline, synced, conn):
    """The check from decision 0001: correctness does not depend on the graph, because SQL gives the same answer."""
    cypher = {(r["gap"], r["source_system"], r["document"]) for r in graph.run_query("chain_gaps")}
    rows = conn.execute("""
        select 'INVOICE_WITHOUT_PO' as gap, i.source_system, i.invoice_id as document
        from core.invoice_line l join core.supplier_invoice i using (source_system, invoice_id)
        where l.po_number is null and not l.is_deleted
        union
        select 'INVOICE_WITHOUT_RECEIPT', l.source_system, l.invoice_id
        from core.invoice_line l
        join core.po_line p on p.source_system = l.source_system and p.po_number = l.po_number and p.line_number = l.po_line_number
        where p.match_type = 'THREE_WAY' and not exists (
            select 1 from core.grn_line g where g.source_system = p.source_system and g.po_number = p.po_number and g.po_line_number = p.line_number)
        union
        select 'PO_WITHOUT_REQUISITION', h.source_system, h.po_number
        from core.purchase_order h
        where not exists (select 1 from core.po_line l where l.source_system = h.source_system and l.po_number = h.po_number and l.pr_number is not null)
        union
        select 'REQUISITION_WITHOUT_APPROVAL', pr.source_system, pr.pr_number
        from core.purchase_requisition pr
        where not pr.is_deleted
          and exists (select 1 from core.po_line l where l.source_system = pr.source_system and l.pr_number = pr.pr_number)
          and not exists (select 1 from core.approval a where a.source_system = pr.source_system and a.pr_number = pr.pr_number)
    """).fetchall()
    sql = {(r["gap"], r["source_system"], r["document"]) for r in rows}
    assert cypher == sql
    assert {gap for gap, _, _ in cypher} == {
        "INVOICE_WITHOUT_PO",
        "INVOICE_WITHOUT_RECEIPT",
        "PO_WITHOUT_REQUISITION",
        "REQUISITION_WITHOUT_APPROVAL",
    }


def test_approval_path_uses_the_limit_at_approval_not_today(pipeline, synced):
    row = next(
        r for r in pipeline.expected if r["scenario_id"] == "S26" and r["document_type"] == "REQUISITION"
    )
    path = graph.run_query(
        "approval_path", source_system=row["source_system"], pr_number=row["document_number"]
    )
    assert path[0]["limit_at_approval"] == "5000.00"
    assert path[0]["limit_today"] == "25000.00"
    assert path[0]["above_limit_unconverted"] is True


def test_approval_path_flags_self_approval(pipeline, synced):
    row = next(
        r for r in pipeline.expected if r["scenario_id"] == "S12" and r["document_type"] == "REQUISITION"
    )
    path = graph.run_query(
        "approval_path", source_system=row["source_system"], pr_number=row["document_number"]
    )
    assert path[0]["self_approved"] is True


def test_supplier_duplicates_are_the_merged_pairs(pipeline, synced):
    merged = [
        r
        for r in pipeline.expected
        if r["document_type"] == "SUPPLIER_PAIR" and r["expected_status"] == "Merged"
    ]
    found = graph.run_query("supplier_duplicates")
    assert len(found) == len(merged)
    assert all(r["across_erps"] for r in found)
    expected_members = {frozenset(r["document_number"].split("|")) for r in merged}
    assert {frozenset(r["members"]) for r in found} == expected_members


def test_golden_invoices_span_both_erps(pipeline, synced, conn):
    s21 = _scenario(pipeline, "S21", "Exception")
    gid = conn.execute(
        """
        select x.supplier_gid from core.supplier_invoice i
        join mdm.supplier_xref x using (source_system, source_supplier_id)
        where i.source_system = %s and i.invoice_id = %s
    """,
        (s21["source_system"], s21["document_number"]),
    ).fetchone()["supplier_gid"]
    invoices = graph.run_query("golden_invoices", supplier_gid=gid)
    assert {r["source_system"] for r in invoices} == {"SAP_LIKE", "ORACLE_LIKE"}
    assert {r["invoice_number"] for r in invoices} >= {"BW-2026-0117", "BW/2026/0117"}


def test_unknown_query_names_are_rejected():
    with pytest.raises(ValueError, match="Unknown query"):
        graph.run_query("does_not_exist")
