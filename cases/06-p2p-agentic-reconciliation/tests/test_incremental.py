"""Reruns are safe, late data is handled, and failed runs leave nothing behind."""

import json
import shutil

import pytest

from p2p_recon import conform, dq, ingest
from p2p_recon.db import connect

RESULT_COLUMNS = "source_system, invoice_id, line_number, status_code, net_amount, expected_amount, cumulative_invoiced, allowed_quantity"


def _snapshot(conn, run_id):
    rows = conn.execute(
        f"select {RESULT_COLUMNS} from recon.match_result where run_id = %s", (run_id,)
    ).fetchall()
    return {(r["source_system"], r["invoice_id"], r["line_number"]): r for r in rows}


def test_redelivering_batches_lands_nothing(pipeline):
    assert pipeline.ingest_rerun_counts["rows_landed"] == 0
    assert pipeline.ingest_rerun_counts["files_new"] == 0
    assert pipeline.raw_rows_after_rerun == pipeline.raw_rows_before_rerun


def test_rerunning_conform_writes_nothing(pipeline):
    for entity, counts in pipeline.conform_rerun_counts.items():
        assert (counts["inserted"], counts["updated"]) == (0, 0), entity


def test_rerun_produces_identical_results(pipeline, conn):
    assert _snapshot(conn, pipeline.run_rerun) == _snapshot(conn, pipeline.run_batch_2)


def test_current_view_has_one_row_per_invoice_line_after_many_runs(pipeline, conn):
    row = conn.execute("""
        select count(*) as rows, count(distinct (source_system, invoice_id, line_number)) as lines,
               (select count(*) from core.invoice_line where not is_deleted) as core_lines
        from recon.current_match_result
    """).fetchone()
    assert row["rows"] == row["lines"] == row["core_lines"]


def test_batch_2_changes_only_the_affected_results(pipeline, conn):
    """The late correction moves exactly the lines it should. Everything else stays as it was, value for value."""
    before = _snapshot(conn, pipeline.run_batch_1)
    after = _snapshot(conn, pipeline.run_batch_2)
    changed = {key for key in before if key in after and before[key] != after[key]}
    added = set(after) - set(before)
    removed = set(before) - set(after)

    def lines(*scenarios):
        return {
            (r["source_system"], r["document_number"], int(r["line_number"]))
            for r in pipeline.expected
            if r["scenario_id"] in scenarios and r["document_type"] == "INVOICE_LINE"
        }

    assert changed == lines("S17", "S29")
    assert added == lines("S20", "S31")
    assert not removed


def test_late_receipt_correction_replaces_the_quantity(pipeline, conn):
    row = next(r for r in pipeline.expected if r["scenario_id"] == "S17" and r["phase"] == "final")
    received = conn.execute(
        "select cumulative_invoiced, allowed_quantity from recon.current_match_result where source_system = %s and invoice_id = %s",
        (row["source_system"], row["document_number"]),
    ).fetchone()
    assert received["allowed_quantity"] == received["cumulative_invoiced"] == 100


def test_stale_out_of_order_row_does_not_overwrite(pipeline, conn):
    row = next(r for r in pipeline.expected if r["expected_status"] == "StaleIgnored")
    expected_quantity = row["detail"].split("=")[1]
    found = conn.execute(
        "select quantity from core.po_line where source_system = %s and po_number = %s and line_number = %s",
        (row["source_system"], row["document_number"], int(row["line_number"])),
    ).fetchone()
    assert str(found["quantity"]) == expected_quantity
    # The stale row did land: raw keeps everything that was delivered.
    landed = conn.execute(
        "select count(*) as n from raw.sap_po_item where ebeln = %s", (row["document_number"],)
    ).fetchone()
    assert landed["n"] == 2


def test_cdc_delete_is_a_soft_delete_and_leaves_controls(pipeline, conn):
    row = next(r for r in pipeline.expected if r["expected_status"] == "Deleted")
    found = conn.execute(
        "select is_deleted from core.purchase_requisition where source_system = %s and pr_number = %s",
        (row["source_system"], row["document_number"]),
    ).fetchone()
    assert found["is_deleted"] is True


def test_quarantine_opened_and_cleared_when_the_parent_arrives(pipeline, conn):
    rows = conn.execute("""
        select entity, reason, resolved_run_id from ops.quarantine order by entity
    """).fetchall()
    by_entity = {r["entity"] for r in rows}
    assert {"purchase_order", "po_line", "grn_line", "supplier_invoice", "invoice_line"} <= by_entity
    assert all(r["resolved_run_id"] is not None for r in rows), (
        "all quarantine should have cleared by batch 2"
    )
    invoice_reason = next(r["reason"] for r in rows if r["entity"] == "supplier_invoice")
    assert "core.supplier" in invoice_reason


def test_lineage_from_core_back_to_the_delivered_file(pipeline, conn):
    row = next(r for r in pipeline.expected if r["scenario_id"] == "S17" and r["phase"] == "final")
    grn = conn.execute(
        """
        select g.source_raw_id, r.batch_id, r.source_file, r.file_row, r.quantity_received
        from core.grn_line g
        join core.invoice_line l on l.source_system = g.source_system and l.po_number = g.po_number and l.po_line_number = g.po_line_number
        join raw.ora_receipts r on r.raw_id = g.source_raw_id
        where l.source_system = %s and l.invoice_id = %s
    """,
        (row["source_system"], row["document_number"]),
    ).fetchone()
    assert grn["batch_id"] == "batch_002"
    assert grn["source_file"] == "oracle/ora_receipts.csv"
    assert grn["quantity_received"] == "100.000"


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (lambda m, d: m["files"][0].__setitem__("row_count", 999), "rows landed"),
        (lambda m, d: m["files"][1].__setitem__("sha256", "0" * 64), "checksum"),
    ],
    ids=["truncated-delivery", "corrupted-file"],
)
def test_bad_delivery_fails_atomically_and_is_recorded(pipeline, conn, tmp_path, tamper, message):
    """An interrupted or corrupt delivery lands nothing, the run is recorded as failed, and a good rerun can follow."""
    batch = tmp_path / "batch_bad"
    shutil.copytree(pipeline.data_dir / "batch_002", batch)
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    manifest["batch_id"] = "batch_bad"
    tamper(manifest, batch)
    (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ingest.DeliveryError, match=message):
        ingest.ingest_batch(batch)

    landed = conn.execute(
        "select count(*) as n from ops.ingest_file where batch_id = 'batch_bad'"
    ).fetchone()["n"]
    assert landed == 0
    raw_rows = conn.execute(
        "select count(*) as n from raw.ora_receipts where batch_id = 'batch_bad'"
    ).fetchone()["n"]
    assert raw_rows == 0
    last = conn.execute(
        "select status, error_message from ops.pipeline_run where step = 'ingest' order by run_id desc limit 1"
    ).fetchone()
    assert last["status"] == "failed"
    assert message in last["error_message"]


def test_schema_drift_is_rejected(pipeline, tmp_path):
    batch = tmp_path / "batch_drift"
    shutil.copytree(pipeline.data_dir / "batch_002", batch)
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    manifest["batch_id"] = "batch_drift"
    entry = manifest["files"][0]
    path = batch / entry["path"]
    text = path.read_text(encoding="utf-8").replace("quantity_received", "qty_received")
    path.write_text(text, encoding="utf-8", newline="\n")
    import hashlib

    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ingest.DeliveryError, match="header differs"):
        ingest.ingest_batch(batch)


def test_row_older_than_the_lookback_is_caught_by_reconciliation(pipeline):
    """The documented limit of watermarking: a very late row is missed by conform. The row count says so."""
    from p2p_recon.generate import ora_ts

    with connect() as admin:
        admin.execute(
            """
            insert into raw.ora_receipts (run_id, source_system, batch_id, source_file, file_row, extracted_at, cdc_op,
                                          receipt_num, line_num, org_id, transaction_date, po_number, po_line_num,
                                          quantity_received, last_update_date)
            select run_id, source_system, 'test', 'test', 1, extracted_at, 'I', 'VERY-LATE', 1, org_id, transaction_date,
                   po_number, po_line_num, quantity_received, %s
            from raw.ora_receipts limit 1
        """,
            (ora_ts(__import__("datetime").datetime(2026, 1, 15)),),
        )
    try:
        conform.conform()
        checks = {(c.name, c.scope): c for c in dq.run_dq()}
        assert checks[("row_count.grn_line", "")].status == "fail"
    finally:
        with connect() as admin:
            admin.execute("delete from raw.ora_receipts where receipt_num = 'VERY-LATE'")
    assert {(c.name, c.scope): c for c in dq.run_dq()}[("row_count.grn_line", "")].status == "pass"
