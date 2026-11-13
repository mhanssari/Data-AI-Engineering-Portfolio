"""Reconciliation as of an arrival date: build core, classify every payment due and every unmatched line, raise
exceptions, compute control totals, and check the invariants, all in one transaction (decision 0005).

The run is logged in ops.run outside that transaction, so a failed run is recorded even though its results are
rolled back.
"""

from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from payrecon import core
from payrecon.config import CASE_ROOT

RECONCILE_SQL = CASE_ROOT / "sql" / "recon" / "reconcile.sql"
CHECKS_SQL = CASE_ROOT / "sql" / "recon" / "checks.sql"


class CheckFailed(Exception):
    """An invariant did not hold; the run's results were rolled back."""

    def __init__(self, violations: list[tuple[str, str]]):
        self.violations = violations
        super().__init__("; ".join(f"{name}: {detail}" for name, detail in violations))


def reconcile(con: psycopg.Connection, as_of_arrival: date) -> dict:
    """Run reconciliation as of `as_of_arrival`; return a summary. Raises CheckFailed if an invariant fails."""
    run_id = con.execute(
        "insert into ops.run (as_of_arrival) values (%s) returning run_id", [as_of_arrival]
    ).fetchone()[0]
    try:
        with con.transaction():
            core_counts = core.build(con, as_of_arrival)
            con.execute("select set_config('payrecon.as_of_arrival', %s, true)", [as_of_arrival.isoformat()])
            con.execute(RECONCILE_SQL.read_text(encoding="utf-8"))
            violations = con.execute(CHECKS_SQL.read_text(encoding="utf-8")).fetchall()
            if violations:
                raise CheckFailed(violations)
            summary = {
                "core": core_counts,
                "payments": dict(
                    con.execute(
                        "select status, count(*) from recon.payment_settlement where as_of_arrival = %s "
                        "group by status order by status",
                        [as_of_arrival],
                    ).fetchall()
                ),
                "unmatched_lines": dict(
                    con.execute(
                        "select status, count(*) from recon.unmatched_line where as_of_arrival = %s "
                        "group by status order by status",
                        [as_of_arrival],
                    ).fetchall()
                ),
                "open_exceptions": con.execute(
                    "select count(*) from recon.exception where as_of_arrival = %s", [as_of_arrival]
                ).fetchone()[0],
            }
    except Exception as error:
        con.execute(
            "update ops.run set status = 'failed', finished_at = now(), detail = %s where run_id = %s",
            [Jsonb({"error": str(error)}), run_id],
        )
        raise
    con.execute(
        "update ops.run set status = 'succeeded', finished_at = now(), detail = %s where run_id = %s",
        [Jsonb(summary), run_id],
    )
    return summary
