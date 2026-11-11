"""Write every mart, and the data-quality history, to Parquet for the Power BI report.

Parquet keeps types (dates, decimals, booleans) and needs no driver or credential in Power BI. The files are a
read-only copy: they are overwritten on every export and never read back by the pipeline.
"""

from pathlib import Path

import duckdb

EXPORTED_SCHEMAS = ("marts",)
EXTRA_TABLES = ("audit.dq_test_result",)


def export_marts(warehouse: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        placeholders = ", ".join("?" for _ in EXPORTED_SCHEMAS)
        tables = [
            f"{schema}.{name}"
            for schema, name in con.execute(
                "select table_schema, table_name from information_schema.tables "
                f"where table_schema in ({placeholders}) and table_type = 'BASE TABLE' order by table_name",
                list(EXPORTED_SCHEMAS),
            ).fetchall()
        ]
        written = []
        for table in [*tables, *EXTRA_TABLES]:
            path = out_dir / f"{table.split('.')[-1]}.parquet"
            con.execute(f"copy {table} to '{path.as_posix()}' (format parquet)")
            written.append(path)
        return written
    finally:
        con.close()
