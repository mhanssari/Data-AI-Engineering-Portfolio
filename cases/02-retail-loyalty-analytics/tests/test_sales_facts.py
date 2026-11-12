"""Sales facts: planted outcomes, and the incremental fact against a full refresh."""

import shutil
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from conftest import query

from loyalty import transform
from loyalty.compare import differences as _differences

FACTS = ("fct_sales_line", "fct_transaction")


def _copy(warehouse: Path, folder: Path) -> Path:
    # Same file name: dbt-duckdb names the catalog after the file, and the views refer to it.
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "loyalty.duckdb"
    shutil.copy(warehouse, target)
    return target


def _full_refresh(warehouse: Path) -> None:
    transform.run_dbt(["build", "--full-refresh", "--select", "fct_sales_line+"], warehouse)


@pytest.fixture(scope="session")
def full_refresh(after_batches, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The incrementally built warehouse after batch 2, rebuilt from scratch."""
    _, incremental = after_batches
    rebuilt = _copy(incremental, tmp_path_factory.mktemp("full_refresh"))
    _full_refresh(rebuilt)
    return rebuilt


# ------------------------------------------------------------------------------------------- planted outcomes


def test_several_loyalty_events_do_not_multiply_revenue(after_batches) -> None:
    _, warehouse = after_batches
    sql = "select net_amount, line_count from marts.fct_transaction where transaction_id = 'T9000003'"
    assert query(warehouse, sql) == [(40, 1)]  # D3: three events, one £40 sale


def test_returns_reduce_sales_on_both_date_bases(after_batches) -> None:
    after_1, after_2 = after_batches
    family = "('T9000004', 'T9000005', 'T9000006')"
    by_attributed = f"""
        select cast(attributed_sale_date as varchar), sum(net_amount)
        from marts.fct_sales_line where transaction_id in {family} group by 1
    """
    by_transaction = f"""
        select cast(transaction_date as varchar), sum(net_amount)
        from marts.fct_sales_line where transaction_id in {family} group by 1 order by 1
    """
    assert query(after_1, by_attributed) == [("2026-08-20", 45)]  # D4: partial return in batch 1
    assert query(after_2, by_attributed) == [("2026-08-20", 15)]  # the rest of it, returned late
    assert query(after_2, by_transaction) == [("2026-08-20", 60), ("2026-09-01", -15), ("2026-09-22", -30)]


def test_an_unmatched_return_is_kept_on_its_own_date(after_batches) -> None:
    _, warehouse = after_batches
    sql = """
        select cast(attributed_sale_date as varchar), net_amount, is_unmatched_return
        from marts.fct_sales_line where transaction_id = 'T9000007'
    """
    assert query(warehouse, sql) == [("2026-02-14", -12, True)]  # D5
    assert query(warehouse, "select count(*) from marts.fct_sales_line where is_unmatched_return") == [(1,)]


def test_an_over_return_is_flagged_and_counted(after_batches) -> None:
    _, warehouse = after_batches
    sql = "select net_amount, is_over_return from marts.fct_sales_line where transaction_id = 'T9000009'"
    assert query(warehouse, sql) == [(-24, True)]  # D6
    assert query(warehouse, "select count(*) from marts.fct_sales_line where is_over_return") == [(1,)]


def test_an_anonymous_sale_counts_in_sales_but_not_for_any_member(after_batches) -> None:
    _, warehouse = after_batches
    sql = """
        select member_id, is_member_sale, net_amount from marts.fct_transaction where transaction_id = 'T9000010'
    """
    assert query(warehouse, sql) == [(None, False, Decimal("9.99"))]  # D7
    member_months = """
        select count(*) from marts.fct_member_month mm
        join marts.fct_sales_line l on l.member_id = mm.member_id where l.transaction_id = 'T9000010'
    """
    assert query(warehouse, member_months) == [(0,)]


def test_sales_use_the_store_version_in_force(after_batches) -> None:
    _, warehouse = after_batches
    sql = """
        select transaction_id, store_version_id from marts.fct_sales_line
        where transaction_id in ('T9000011', 'T9000012') order by 1
    """
    assert query(warehouse, sql) == [("T9000011", "S003@2015-03-09"), ("T9000012", "S003@2025-09-01")]  # D9


def test_a_member_claimed_late_owns_the_sale(after_batches) -> None:
    after_1, after_2 = after_batches
    sql = "select member_id, is_member_sale from marts.fct_transaction where transaction_id = 'T9000014'"
    assert query(after_1, sql) == [(None, False)]
    assert query(after_2, sql) == [("M90003", True)]  # D11


# ------------------------------------------------------------------------------------------- incremental


@pytest.mark.parametrize("table", FACTS)
def test_incremental_equals_full_refresh_after_late_corrections(after_batches, full_refresh, table) -> None:
    _, incremental = after_batches
    assert query(incremental, f"select count(*) from marts.{table}") == query(
        full_refresh, f"select count(*) from marts.{table}"
    )
    assert _differences(incremental, full_refresh, table) == 0  # A6


def test_the_incremental_run_rewrote_only_what_changed(after_batches) -> None:
    _, warehouse = after_batches
    runs = query(
        warehouse,
        "select built_in_invocation_id, count(*) from marts.fct_sales_line group by 1 order by 2",
    )
    assert len(runs) == 2
    (batch_2_run, rewritten), (_, untouched) = runs
    assert rewritten < 0.05 * (rewritten + untouched)
    claimed = f"""
        select count(*) from marts.fct_sales_line
        where built_in_invocation_id = '{batch_2_run}' and transaction_id in ('T9000014', 'T9000004', 'T9000005')
    """
    # The late member claim, and the earlier return of a sale whose last return arrived in batch 2. The sale
    # itself is not rewritten: nothing on a sale line depends on its returns.
    assert query(warehouse, claimed) == [(2,)]


def test_a_rerun_with_nothing_new_changes_nothing(after_batches, tmp_path: Path) -> None:
    _, warehouse = after_batches
    rerun = _copy(warehouse, tmp_path)
    transform.run_dbt(["build", "--select", "fct_sales_line+"], rerun)
    for table in FACTS:
        assert _differences(warehouse, rerun, table) == 0  # A5
    before = query(
        warehouse, "select built_in_invocation_id, count(*) from marts.fct_sales_line group by 1 order by 1"
    )
    after = query(
        rerun, "select built_in_invocation_id, count(*) from marts.fct_sales_line group by 1 order by 1"
    )
    assert before == after  # no row was rewritten


def test_a_sale_arriving_after_its_return_relinks_the_return(after_batches, tmp_path: Path) -> None:
    _, warehouse = after_batches
    late = _copy(warehouse, tmp_path / "late")
    # D5's missing sale, delivered after batch 2: the return already in the fact must be re-linked to it.
    con = duckdb.connect(str(late))
    con.execute(
        "insert into raw.pos_transactions values ('T8999999', 'S006', null, '2026-01-20 12:00:00', 'sale', null, "
        "'2026-01-20 12:00:00', 999, 3, 'manual', 'manual', current_timestamp::timestamp)"
    )
    con.execute(
        "insert into raw.pos_transaction_lines values ('T8999999', '1', 'P006', '1', '12.00', '0.00', 'GBP', null, "
        "999, 3, 'manual', 'manual', current_timestamp::timestamp)"
    )
    con.close()
    transform.run_dbt(["build"], late)
    sql = """
        select cast(attributed_sale_date as varchar), is_unmatched_return
        from marts.fct_sales_line where transaction_id = 'T9000007'
    """
    assert query(late, sql) == [("2026-01-20", False)]
    rebuilt = _copy(late, tmp_path / "rebuilt")
    _full_refresh(rebuilt)
    for table in FACTS:
        assert _differences(late, rebuilt, table) == 0


def test_a_store_version_arriving_late_repairs_the_sales_it_covers(after_batches, tmp_path: Path) -> None:
    _, warehouse = after_batches
    late = _copy(warehouse, tmp_path / "late")
    # S001 is moved to a new region with effect from 1 January 2026, delivered after batch 2.
    con = duckdb.connect(str(late))
    con.execute(
        "insert into raw.reference_stores "
        "select store_id, store_name, 'South East', store_format, opened_date, '2026-01-01', '2026-10-01 09:00:00', "
        "999, 3, 'manual', 'manual', current_timestamp::timestamp "
        "from raw.reference_stores where store_id = 'S001' limit 1"
    )
    con.close()
    # The build runs invariant 6, which fails if any sale still points at the version that ended.
    transform.run_dbt(["build"], late)
    repaired = query(
        late,
        "select count(*) from marts.fct_sales_line where store_version_id = 'S001@2026-01-01' "
        "and transaction_date < '2026-09-17'",
    )
    assert repaired[0][0] > 0
    rebuilt = _copy(late, tmp_path / "rebuilt")
    _full_refresh(rebuilt)
    for table in FACTS:
        assert _differences(late, rebuilt, table) == 0
