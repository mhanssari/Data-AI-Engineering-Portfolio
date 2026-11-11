"""Test results are recorded with their failing rows; the Parquet export is a faithful copy of the marts."""

import shutil
from pathlib import Path

import duckdb
import pytest
from conftest import query

from loyalty import export, reference, transform
from loyalty.config import CASE_ROOT


def test_every_test_result_is_recorded(after_batches) -> None:
    _, warehouse = after_batches
    sql = """
        select test_kind, count(*) filter (where status <> 'pass')
        from audit.dq_test_result
        where invocation_id = (select max_by(invocation_id, run_started_at) from audit.dq_test_result)
        group by 1 order by 1
    """
    assert query(warehouse, sql) == [("generic", 0), ("singular", 0), ("unit", 0)]
    runs = "select count(distinct invocation_id) from audit.dq_test_result"
    assert query(warehouse, runs) == [(2,)]  # one per batch: history accumulates


def test_a_failing_test_leaves_its_result_and_its_failing_rows(after_batches, tmp_path: Path) -> None:
    _, warehouse = after_batches
    broken = tmp_path / "loyalty.duckdb"
    shutil.copy(warehouse, broken)
    con = duckdb.connect(str(broken))
    con.execute(
        "update marts.fct_transaction set net_amount = net_amount + 1 where transaction_id = 'T9000003'"
    )
    con.close()
    with pytest.raises(transform.DbtError):
        transform.run_dbt(["test", "--select", "assert_transactions_equal_the_sum_of_their_lines"], broken)
    recorded = query(
        broken,
        "select status, failure_count, failures_relation from audit.dq_test_result "
        "where test_name = 'assert_transactions_equal_the_sum_of_their_lines' order by run_started_at desc limit 1",
    )
    status, failures, relation = recorded[0]
    assert (status, failures) == ("fail", 1)  # A14
    assert query(broken, f"select transaction_id from {relation}") == [("T9000003",)]


def test_every_reconciliation_query_runs_on_the_export(after_batches, tmp_path: Path) -> None:
    """The SQL side of the Power BI reconciliation (A10) runs in CI, where Power BI cannot."""
    _, warehouse = after_batches
    export.export_marts(warehouse, tmp_path)
    values = reference.reference_values(tmp_path, CASE_ROOT / "bi" / "reconciliation.json")
    assert values["Trading days, S012, June 2025 (opened 16 June)"] == 15  # the metric catalogue's example
    assert values["Status conflicts"] == 2  # D13 and D14
    assert values["As-of date"] == "2026-09-30"
    blanks = {name for name, value in values.items() if value is None}
    assert blanks == {
        "Repeat purchase rate, August 2026 cohort (immature: blank)",
        "Repeat purchase rate, all cohorts (some immature: blank)",
        "RLS denied: Region North sees nothing of S001 (London)",
    }


def test_the_export_is_a_faithful_copy_of_the_marts(after_batches, tmp_path: Path) -> None:
    _, warehouse = after_batches
    written = export.export_marts(warehouse, tmp_path)
    marts = query(warehouse, "select table_name from information_schema.tables where table_schema = 'marts'")
    assert {p.stem for p in written} == {name for (name,) in marts} | {"dq_test_result"}
    con = duckdb.connect()
    try:
        parquet = (tmp_path / "fct_sales_line.parquet").as_posix()
        exported = con.execute(f"select count(*), sum(net_amount) from '{parquet}'").fetchone()
        net_type = con.execute(f"select typeof(net_amount) from '{parquet}' limit 1").fetchone()[0]
    finally:
        con.close()
    assert exported == query(warehouse, "select count(*), sum(net_amount) from marts.fct_sales_line")[0]
    assert net_type == "DECIMAL(18,2)"  # money stays exact in Power BI, not floating point
