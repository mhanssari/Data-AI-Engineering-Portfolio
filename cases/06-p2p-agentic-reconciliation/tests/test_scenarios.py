"""Every planted scenario reaches its expected outcome, and nothing outside them raises an exception."""

from decimal import Decimal

import pytest

from p2p_recon.generate import generate
from p2p_recon.ontology import concept_codes


def _result(conn, run_id, row):
    return conn.execute(
        "select status_code from recon.match_result where run_id = %s and source_system = %s and invoice_id = %s and line_number = %s",
        (run_id, row["source_system"], row["document_number"], int(row["line_number"])),
    ).fetchone()


def _reasons(conn, run_id, row):
    rows = conn.execute(
        "select reason_code from recon.exception where run_id = %s and source_system = %s and invoice_id = %s and line_number = %s",
        (run_id, row["source_system"], row["document_number"], int(row["line_number"])),
    ).fetchall()
    return {r["reason_code"] for r in rows}


def _assert_invoice_line(conn, run_id, row):
    label = f"{row['scenario_id']} {row['description']} ({row['document_number']}/{row['line_number']})"
    if row["expected_status"] == "Quarantined":
        assert _result(conn, run_id, row) is None, label
        return
    result = _result(conn, run_id, row)
    assert result is not None, f"{label}: no match result"
    assert result["status_code"] == row["expected_status"], label
    # Exact set, so an over-firing rule fails as well as a missing one.
    assert _reasons(conn, run_id, row) == {c for c in row["expected_reason_codes"].split("|") if c}, label


def test_all_scenarios_are_planted(pipeline):
    planted = {row["scenario_id"] for row in pipeline.expected}
    assert planted == {f"S{n:02d}" for n in range(1, 37)}


def test_every_status_and_every_reason_is_exercised(pipeline):
    """A reason code no scenario reaches is a rule with no test."""
    lines = [r for r in pipeline.expected if r["document_type"] == "INVOICE_LINE"]
    statuses = {r["expected_status"] for r in lines} - {"Quarantined"}
    assert statuses == concept_codes("MatchStatus")
    reasons = {c for r in lines for c in r["expected_reason_codes"].split("|") if c}
    grouping_concepts = {"MatchingException", "ControlException", "RiskSignal"}
    assert reasons == concept_codes("ExceptionReason") - grouping_concepts


def test_invoice_lines_after_batch_1(pipeline, conn):
    for row in pipeline.expected:
        if row["document_type"] == "INVOICE_LINE" and row["phase"] in ("all", "batch_001"):
            _assert_invoice_line(conn, pipeline.run_batch_1, row)


def test_invoice_lines_after_batch_2(pipeline, conn):
    for row in pipeline.expected:
        if row["document_type"] == "INVOICE_LINE" and row["phase"] in ("all", "final"):
            _assert_invoice_line(conn, pipeline.run_batch_2, row)


def test_control_breaches_recorded_on_the_requisition(pipeline, conn):
    for row in (
        r
        for r in pipeline.expected
        if r["document_type"] == "REQUISITION" and r["expected_status"] in ("ControlBreach", "NoBreach")
    ):
        run_id = pipeline.run_batch_1 if row["phase"] == "batch_001" else pipeline.run_batch_2
        found = conn.execute(
            "select reason_code from recon.control_breach where run_id = %s and source_system = %s and pr_number = %s",
            (run_id, row["source_system"], row["document_number"]),
        ).fetchall()
        expected = {row["expected_reason_codes"]} if row["expected_status"] == "ControlBreach" else set()
        assert {r["reason_code"] for r in found} == expected, row["scenario_id"]


def test_no_false_positives_outside_the_planted_scenarios(pipeline, conn):
    """The background is clean by construction, so any exception here is a rule firing wrongly."""
    planted = {
        (r["source_system"], r["document_number"], int(r["line_number"]))
        for r in pipeline.expected
        if r["document_type"] == "INVOICE_LINE"
    }
    rows = conn.execute(
        "select source_system, invoice_id, line_number, status_code from recon.match_result where run_id = %s",
        (pipeline.run_batch_2,),
    ).fetchall()
    background = [r for r in rows if (r["source_system"], r["invoice_id"], r["line_number"]) not in planted]
    assert len(background) > 200, "the background should be the bulk of the data"
    unexpected = [r for r in background if r["status_code"] not in ("Matched", "MatchedWithinTolerance")]
    assert not unexpected, unexpected[:10]
    # And the tolerance path is genuinely used by the background.
    assert any(r["status_code"] == "MatchedWithinTolerance" for r in background)


def test_no_control_breach_outside_the_planted_scenarios(pipeline, conn):
    planted = {
        (r["source_system"], r["document_number"])
        for r in pipeline.expected
        if r["expected_status"] == "ControlBreach" and r["phase"] != "batch_001"
    }
    rows = conn.execute(
        "select source_system, pr_number, reason_code from recon.control_breach where run_id = %s",
        (pipeline.run_batch_2,),
    ).fetchall()
    unexpected = [r for r in rows if (r["source_system"], r["pr_number"]) not in planted]
    assert not unexpected, unexpected


def test_oracle_submit_action_is_not_treated_as_self_approval(pipeline, conn):
    """Every Oracle requisition has a SUBMIT row by its preparer; only planted S12 is a real breach."""
    count = conn.execute(
        "select count(*) as n from recon.control_breach where run_id = %s and source_system = 'ORACLE_LIKE' and reason_code = 'SegregationOfDuties'",
        (pipeline.run_batch_2,),
    ).fetchone()["n"]
    assert count == 1


def test_grni_at_cut_off(pipeline, conn):
    rows = [r for r in pipeline.expected if r["expected_status"] == "InGRNI"]
    assert {r["scenario_id"] for r in rows} == {"S16", "S19", "S33"}
    for row in rows:
        detail = dict(item.split("=") for item in row["detail"].split(";"))
        found = conn.execute(
            "select grni_quantity, grni_amount, currency_code from recon.grni_line "
            "where run_id = %s and source_system = %s and po_number = %s and line_number = %s",
            (pipeline.run_batch_2, row["source_system"], row["document_number"], int(row["line_number"])),
        ).fetchone()
        assert found is not None, row["scenario_id"]
        assert found["grni_quantity"] == Decimal(detail["grni_quantity"]), row["scenario_id"]
        assert found["grni_amount"] == Decimal(detail["grni_amount"]), row["scenario_id"]
        assert found["currency_code"] == {"GB01": "GBP", "DE01": "EUR"}[row["company_code"]], row[
            "scenario_id"
        ]


def test_rejected_credit_note_creates_no_accrual(pipeline, conn):
    row = next(r for r in pipeline.expected if r["expected_status"] == "NotInGRNI")
    found = conn.execute(
        "select 1 from recon.grni_line where run_id = %s and source_system = %s and po_number = %s",
        (pipeline.run_batch_2, row["source_system"], row["document_number"]),
    ).fetchone()
    assert found is None


def test_grni_excludes_receipts_after_the_cut_off(pipeline, conn):
    """Scenario S31's goods were received on 1 October, after the 30 September cut-off, and matched by an invoice anyway."""
    s31 = next(r for r in pipeline.expected if r["scenario_id"] == "S31")
    po = conn.execute(
        "select po_number, po_line_number from core.invoice_line where source_system = %s and invoice_id = %s",
        (s31["source_system"], s31["document_number"]),
    ).fetchone()
    found = conn.execute(
        "select 1 from recon.grni_line where run_id = %s and source_system = %s and po_number = %s",
        (pipeline.run_batch_2, s31["source_system"], po["po_number"]),
    ).fetchone()
    assert found is None


def test_accrual_journal_balances_and_uses_the_clearing_account(pipeline, conn):
    rows = conn.execute(
        "select company_code, currency_code, gl_account_code, side, amount from recon.accrual_journal_line where run_id = %s",
        (pipeline.run_batch_2,),
    ).fetchall()
    assert rows
    for company, clearing in (("GB01", "210500"), ("DE01", "2105000")):
        credits_ = [r for r in rows if r["company_code"] == company and r["side"] == "C"]
        debits = [r for r in rows if r["company_code"] == company and r["side"] == "D"]
        assert {r["gl_account_code"] for r in credits_} == {clearing}
        assert sum(r["amount"] for r in credits_) == sum(r["amount"] for r in debits)


def test_payment_readiness_follows_the_lines(pipeline, conn):
    rows = conn.execute("select readiness, lines_blocking from recon.current_payment_readiness").fetchall()
    assert all((r["readiness"] == "Held") == (r["lines_blocking"] > 0) for r in rows)
    assert {r["readiness"] for r in rows} == {"Held", "ReadyForApproval"}


@pytest.mark.parametrize("seed", [6042])
def test_generator_is_deterministic(tmp_path, seed):
    import hashlib

    def digest(folder):
        return {
            str(f.relative_to(folder)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in folder.rglob("*")
            if f.is_file()
        }

    generate(tmp_path / "a", seed)
    generate(tmp_path / "b", seed)
    assert digest(tmp_path / "a") == digest(tmp_path / "b")
