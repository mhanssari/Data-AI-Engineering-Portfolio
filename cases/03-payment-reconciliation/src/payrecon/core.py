"""Core build: deduplicated events, payment state, illegal transitions and settlement lines, as of an arrival date.

The SQL lives in sql/core/build.sql so it can be read on its own. It is rebuilt whole, in one transaction, from
the raw rows of files that had arrived by the given date (decision 0004).
"""

from datetime import date

import psycopg

from payrecon.config import CASE_ROOT

BUILD_SQL = CASE_ROOT / "sql" / "core" / "build.sql"
TABLES = (
    "core.merchant",
    "core.payment_event",
    "core.payment",
    "core.illegal_transition",
    "core.settlement_line",
)


def latest_arrival(con: psycopg.Connection) -> date | None:
    return con.execute("select max(arrival_date) from ops.file").fetchone()[0]


def build(con: psycopg.Connection, as_of_arrival: date) -> dict[str, int]:
    """Rebuild core from everything that had arrived by `as_of_arrival`; return each table's row count."""
    with con.transaction():
        # A transaction-local setting carries the date into a multi-statement script (bind parameters cannot).
        con.execute("select set_config('payrecon.as_of_arrival', %s, true)", [as_of_arrival.isoformat()])
        con.execute(BUILD_SQL.read_text(encoding="utf-8"))
        return {table: con.execute(f"select count(*) from {table}").fetchone()[0] for table in TABLES}
