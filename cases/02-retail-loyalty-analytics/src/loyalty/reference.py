"""Reference values for the Power BI reconciliation (acceptance A10).

Runs the SQL side of each check in bi/reconciliation.json with DuckDB over the same Parquet files Power BI reads,
so the comparison tests the semantic model and its DAX, not two different copies of the data.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb


def _plain(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def reference_values(exports: Path, checks_file: Path) -> dict[str, object]:
    checks = json.loads(checks_file.read_text(encoding="utf-8"))["checks"]
    con = duckdb.connect()
    try:
        for parquet in sorted(exports.glob("*.parquet")):
            con.execute(f"create view {parquet.stem} as select * from read_parquet('{parquet.as_posix()}')")
        return {check["name"]: _plain(con.execute(check["sql"]).fetchone()[0]) for check in checks}
    finally:
        con.close()


def write_reference_values(exports: Path, checks_file: Path, out: Path) -> Path:
    values = reference_values(exports, checks_file)
    out.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    return out
