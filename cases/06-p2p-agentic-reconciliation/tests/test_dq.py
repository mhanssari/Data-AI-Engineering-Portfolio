"""Data-quality checks pass on good data, and the reconciliations can actually fail."""

from p2p_recon import dq
from p2p_recon.db import connect


def test_all_checks_pass_after_both_batches(dq_results):
    failed = [c for c in dq_results if c.status == "fail"]
    assert not failed, failed


def test_control_totals_cover_both_companies_and_every_currency(dq_results):
    scopes = {c.scope for c in dq_results if c.name == "control_total.invoice_net_amount"}
    assert {"GB01/GBP", "GB01/EUR", "DE01/EUR", "DE01/GBP"} <= scopes
    assert all(c.expected == c.actual for c in dq_results if c.name.startswith("control_total."))


def test_results_are_recorded_in_ops(dq_results, conn):
    row = conn.execute("""
        select count(*) as n from ops.dq_result
        where run_id = (select max(run_id) from ops.pipeline_run where step = 'dq' and status = 'succeeded')
    """).fetchone()
    assert row["n"] == len(dq_results)


def test_control_total_detects_a_core_amount_changed_outside_the_pipeline(pipeline):
    """A reconciliation that cannot fail asserts nothing, so prove it fails, then restore."""
    with connect() as admin:
        target = admin.execute("""
            select source_system, invoice_id, company_code, currency_code, net_amount from core.supplier_invoice
            where document_type = 'INV' and not is_deleted order by source_system, invoice_id limit 1
        """).fetchone()
        admin.execute(
            "update core.supplier_invoice set net_amount = net_amount + 0.01 where source_system = %s and invoice_id = %s",
            (target["source_system"], target["invoice_id"]),
        )
    try:
        checks = {(c.name, c.scope): c for c in dq.run_dq()}
        scope = f"{target['company_code']}/{target['currency_code'].strip()}"
        assert checks[("control_total.invoice_net_amount", scope)].status == "fail"
        assert checks[("invoice_header_equals_lines", "")].status == "fail"
    finally:
        with connect() as admin:
            admin.execute(
                "update core.supplier_invoice set net_amount = %s where source_system = %s and invoice_id = %s",
                (target["net_amount"], target["source_system"], target["invoice_id"]),
            )


def test_quarantine_is_stated_as_the_reconciling_item_after_batch_1(pipeline):
    """After batch 1 the S20 invoice (500.00 EUR) is in quarantine. The total balances only by saying so."""
    checks = {(c.name, c.scope): c for c in pipeline.dq_after_batch_1}
    invoice_total = checks[("control_total.invoice_net_amount", "DE01/EUR")]
    assert invoice_total.status == "pass"
    assert invoice_total.detail == "quarantined 500.00"
    row_count = checks[("row_count.supplier_invoice", "")]
    assert row_count.status == "pass"
    assert row_count.detail == "1 in open quarantine"
    assert checks[("open_quarantine", "")].status == "warn"
