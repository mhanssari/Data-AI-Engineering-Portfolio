"""Compare the marts of two warehouse files row for row.

Used to show that two ways of building the same data give the same answer: incremental against full refresh,
and the Airflow DAG against `loyalty run-all`. Columns that record *which run* wrote a row are ignored.
"""

from pathlib import Path

import duckdb

RUN_METADATA_COLUMNS = ("built_in_invocation_id",)


def differences(left: Path, right: Path, table: str, schema: str = "marts") -> int:
    """Rows in one table but not the other, counted both ways, duplicates included."""
    con = duckdb.connect()
    try:
        con.execute(f"attach '{left.as_posix()}' as a (read_only)")
        con.execute(f"attach '{right.as_posix()}' as b (read_only)")
        columns = [
            name
            for (name,) in con.execute(
                "select column_name from information_schema.columns "
                "where table_catalog = 'a' and table_schema = ? and table_name = ? order by ordinal_position",
                [schema, table],
            ).fetchall()
            if name not in RUN_METADATA_COLUMNS
        ]
        cols = ", ".join(columns)
        a, b = f"a.{schema}.{table}", f"b.{schema}.{table}"
        sql = (
            f"select count(*) from ((select {cols} from {a} except all select {cols} from {b}) "
            f"union all (select {cols} from {b} except all select {cols} from {a}))"
        )
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


def mart_tables(warehouse: Path) -> list[str]:
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        return [
            name
            for (name,) in con.execute(
                "select table_name from information_schema.tables where table_schema = 'marts' order by 1"
            ).fetchall()
        ]
    finally:
        con.close()


def compare_marts(left: Path, right: Path) -> dict[str, int]:
    """Differences per mart; a mart present in only one warehouse counts as -1."""
    left_tables, right_tables = set(mart_tables(left)), set(mart_tables(right))
    result = {table: -1 for table in left_tables ^ right_tables}
    for table in sorted(left_tables & right_tables):
        result[table] = differences(left, right, table)
    return result
