"""Reconciliation: the planted outcomes P2-P16 on the runs where they should appear, the grace rule, the invariant
checks, and rerun safety.

The whole delivery is loaded once (`delivered`); each run is reconciled as of its arrival date. Core and
reconciliation read only files that had arrived by that date, so the order runs are made in does not matter.
"""

from datetime import date

import psycopg
import pytest

from payrecon import core, recon

pytestmark = pytest.mark.db

RUN_DAYS = [date(2026, 10, d) for d in (4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 21, 22, 23)] + [
    date(2026, 11, 1)
]


@pytest.fixture(scope="module")
def runs(delivered: psycopg.Connection) -> psycopg.Connection:
    for day in RUN_DAYS:
        recon.reconcile(delivered, day)
    return delivered


def _status(con, day: str, payment_id: str) -> str | None:
    row = con.execute(
        "select status from recon.payment_settlement where as_of_arrival = %s and payment_id = %s",
        [day, payment_id],
    ).fetchone()
    return row[0] if row else None


def _line_status(con, day: str, line_id: str) -> str | None:
    row = con.execute(
        "select status from recon.unmatched_line where as_of_arrival = %s and settlement_line_id = %s",
        [day, line_id],
    ).fetchone()
    return row[0] if row else None


def _exceptions(con, day: str) -> dict[str, tuple]:
    rows = con.execute(
        "select exception_id, currency, gross_difference_minor, fee_difference_minor "
        "from recon.exception where as_of_arrival = %s",
        [day],
    ).fetchall()
    return {r[0]: tuple(r[1:]) for r in rows}


def test_every_run_explains_every_difference(runs) -> None:
    """Invariant 3, on every run: control totals leave nothing unexplained (the run would have failed otherwise,
    so this also proves the checks ran)."""
    assert runs.execute(
        "select count(distinct as_of_arrival), count(*) filter (where unexplained_minor <> 0) "
        "from recon.control_total"
    ).fetchone() == (len(RUN_DAYS), 0)
    assert runs.execute("select count(*) from ops.run where status = 'succeeded'").fetchone()[0] >= len(
        RUN_DAYS
    )


def test_the_final_run_has_exactly_the_planted_exceptions(runs) -> None:
    """Every exception on the last run is planted, with its signed amount in its own currency. The clean
    background raises none."""
    assert _exceptions(runs, "2026-11-01") == {
        "missing_settlement:pay-9000006": ("GBP", 5600, 0),  # P6
        "unexpected_settlement:stl-EUR-20261007-P7": ("EUR", -7700, 0),  # P7
        "amount_mismatch:pay-9000009": ("USD", 1000, 0),  # P9: 140.00 settled for 150.00
        "fee_mismatch:pay-9000010": ("GBP", 0, 150),  # P10: gross agrees, fee 1.50 over
        "currency_mismatch:pay-9000011": ("GBP", 6100, 0),  # P11: nothing settled in GBP ...
        "currency_mismatch:" + _p11_line(runs): ("EUR", -6100, 0),  # ... and 61.00 settled in EUR
        "duplicate_settlement:pay-9000013": ("EUR", -4400, 0),  # P13: settled twice
    }


def _p11_line(con) -> str:
    return con.execute(
        "select settlement_line_id from core.settlement_line where payment_reference = 'pay-9000011'"
    ).fetchone()[0]


def test_partial_refunded_and_reversed_payments_match(runs) -> None:
    """P8 (two partial lines), P15 (refund after settlement), P16 (reversal), P1, P2 and P5 all end matched; P16's
    stuck authorisation is never due."""
    for payment_id in (
        "pay-9000008",
        "pay-9000015",
        "pay-9000016",
        "pay-9000001",
        "pay-9000002",
        "pay-9000005",
    ):
        assert _status(runs, "2026-11-01", payment_id) == "matched", payment_id
    assert _status(runs, "2026-11-01", "pay-9000017") is None


def test_an_unsettled_payment_is_pending_for_its_grace_day_then_missing(runs) -> None:
    """A6 and invariant 6, planted P6: captured 6 October."""
    assert _status(runs, "2026-10-07", "pay-9000006") == "pending"
    assert _status(runs, "2026-10-08", "pay-9000006") == "missing_settlement"


def test_a_line_whose_payment_is_held_awaits_and_then_matches(runs) -> None:
    """P2: the sale line arrives on 4 October with the capture, but the capture is held until authorised arrives."""
    line = runs.execute(
        "select settlement_line_id from core.settlement_line where payment_reference = 'pay-9000002'"
    ).fetchone()[0]
    assert _line_status(runs, "2026-10-04", line) == "awaiting"
    assert _line_status(runs, "2026-10-05", line) is None
    assert _status(runs, "2026-10-05", "pay-9000002") == "matched"


def test_a_line_with_no_payment_awaits_for_its_grace_day_then_is_an_exception(runs) -> None:
    """P7: the line is dated 7 October."""
    assert _line_status(runs, "2026-10-08", "stl-EUR-20261007-P7") == "awaiting"
    assert _line_status(runs, "2026-10-09", "stl-EUR-20261007-P7") == "unexpected_settlement"


def test_reference_data_later_than_grace_raises_an_exception_that_the_next_run_resolves(runs) -> None:
    """P5: the line (9 October) awaits on 10 October, is unexpected on 11 October because merchant M041 is still
    unknown, and matches on 12 October when M041 arrives."""
    line = runs.execute(
        "select settlement_line_id from core.settlement_line where payment_reference = 'pay-9000005'"
    ).fetchone()[0]
    assert _line_status(runs, "2026-10-10", line) == "awaiting"
    assert _line_status(runs, "2026-10-11", line) == "unexpected_settlement"
    assert _line_status(runs, "2026-10-12", line) is None
    assert _status(runs, "2026-10-12", "pay-9000005") == "matched"


def test_short_partials_are_pending_in_grace_and_a_fee_error_is_raised_at_once(runs) -> None:
    """P9 waits a day (more lines could still come); P10's gross already agrees, so its fee error cannot change."""
    assert _status(runs, "2026-10-10", "pay-9000009") == "pending"
    assert _status(runs, "2026-10-11", "pay-9000009") == "amount_mismatch"
    assert _status(runs, "2026-10-11", "pay-9000010") == "fee_mismatch"


def test_a_double_settlement_is_an_exception_but_a_resent_file_is_not(runs) -> None:
    """A8: P13's second line (14 October) makes it a duplicate on 15 October; P12's re-sent file changes nothing."""
    assert _status(runs, "2026-10-14", "pay-9000013") == "matched"
    assert _status(runs, "2026-10-15", "pay-9000013") == "duplicate_settlement"
    assert _exceptions(runs, "2026-10-15").keys() == _exceptions(runs, "2026-10-14").keys() | {
        "duplicate_settlement:pay-9000013"
    }
    # P12: the GBP file for 13 October was sent twice, yet each of its lines was delivered once.
    assert (
        runs.execute(
            "select count(*), max(delivery_count) from core.settlement_line where currency = 'GBP' "
            "and settlement_date = '2026-10-13'"
        ).fetchone()[1]
        == 1
    )


def test_a_late_file_makes_payments_missing_until_it_arrives(runs) -> None:
    """A6, planted P14: the USD file for 20 October is due on 21 October and arrives on 23 October. Two payments
    refunded on 21 October are still in that refund's grace day on the 22nd. Refund lines in the late file make 24
    older payments look over-settled until it arrives. Counts are exact: the generator is seeded."""
    by_status = (
        "select status, count(*) from recon.payment_settlement where as_of_arrival = %s "
        "and currency = 'USD' and due_date = '2026-10-20' group by status"
    )
    assert dict(runs.execute(by_status, ["2026-10-21"]).fetchall()) == {"pending": 475}
    assert dict(runs.execute(by_status, ["2026-10-22"]).fetchall()) == {
        "missing_settlement": 473,
        "pending": 2,
    }
    assert dict(runs.execute(by_status, ["2026-10-23"]).fetchall()) == {"matched": 475}
    refunds_in_late_file = (
        "select count(*) from recon.exception where as_of_arrival = %s and currency = 'USD' "
        "and exception_type = 'amount_mismatch' and gross_difference_minor < 0"
    )
    assert runs.execute(refunds_in_late_file, ["2026-10-22"]).fetchone() == (24,)
    assert runs.execute(refunds_in_late_file, ["2026-10-23"]).fetchone() == (0,)
    assert len(_exceptions(runs, "2026-10-23")) == 7  # back to the planted exceptions open by then


def test_amounts_are_never_added_across_currencies(runs) -> None:
    """Invariant 5, planted P11: the EUR line is not counted towards the GBP payment."""
    assert runs.execute(
        "select settled_gross_minor, settlement_lines, foreign_currency_lines "
        "from recon.payment_settlement where as_of_arrival = '2026-11-01' "
        "and payment_id = 'pay-9000011'"
    ).fetchone() == (0, 0, 1)


def test_rerunning_a_day_gives_identical_results(runs) -> None:
    """A7 and invariant 8: rerunning 22 October after everything has arrived replaces it with the same rows."""

    def digest() -> tuple:
        return tuple(
            runs.execute(
                f"select md5(coalesce(string_agg(t::text, '|' order by t::text), '')) from {table} t "
                "where as_of_arrival = '2026-10-22'"
            ).fetchone()[0]
            for table in (
                "recon.payment_settlement",
                "recon.unmatched_line",
                "recon.exception",
                "recon.control_total",
            )
        )

    before = digest()
    recon.reconcile(runs, date(2026, 10, 22))
    assert digest() == before


def test_a_failed_check_rolls_back_the_run_and_is_logged(runs, monkeypatch, tmp_path) -> None:
    """A run whose checks fail publishes nothing: the day's earlier results stay, and ops.run records the failure."""
    forced = tmp_path / "checks.sql"
    forced.write_text("select 'forced failure' as check_name, 'test' as detail")
    monkeypatch.setattr(recon, "CHECKS_SQL", forced)
    before = runs.execute(
        "select count(*) from recon.payment_settlement where as_of_arrival = '2026-10-08'"
    ).fetchone()[0]
    with pytest.raises(recon.CheckFailed, match="forced failure"):
        recon.reconcile(runs, date(2026, 10, 8))
    assert (
        runs.execute(
            "select count(*) from recon.payment_settlement where as_of_arrival = '2026-10-08'"
        ).fetchone()[0]
        == before
    )
    assert runs.execute(
        "select status, detail->>'error' from ops.run order by run_id desc limit 1"
    ).fetchone() == ("failed", "forced failure: test")


@pytest.mark.parametrize(
    "tamper, check",
    [
        (
            "update recon.control_total set pending_minor = pending_minor + 1 "
            "where as_of_arrival = '2026-10-22' and currency = 'USD' and due_date = '2026-10-20'",
            "invariant 3: unexplained difference",
        ),
        (
            "update recon.payment_settlement set status = 'missing_settlement' "
            "where as_of_arrival = '2026-10-22' and status = 'pending'",
            "invariant 6: missing within grace",
        ),
        (
            "update recon.unmatched_line set settlement_date = '2026-10-21' "
            "where as_of_arrival = '2026-10-22' and status = 'unexpected_settlement'",
            "invariant 6: unexpected within grace",
        ),
        (
            "delete from recon.payment_settlement where as_of_arrival = '2026-10-22' and payment_id = 'pay-9000006'",
            "every payment due is classified",
        ),
        (
            "delete from recon.unmatched_line where as_of_arrival = '2026-10-22'",
            "every line is accounted for once",
        ),
    ],
)
def test_each_check_catches_what_it_guards(runs, tamper: str, check: str) -> None:
    """The checks are not decorative: corrupt one result of a passing run and the matching check reports it."""
    with runs.transaction(force_rollback=True):
        core.build(runs, date(2026, 10, 22))  # the completeness checks compare with core as of the same day
        runs.execute("select set_config('payrecon.as_of_arrival', '2026-10-22', true)")
        runs.execute(tamper)
        found = [name for name, _ in runs.execute(recon.CHECKS_SQL.read_text()).fetchall()]
    assert check in found
