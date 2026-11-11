"""Batches replayed in order through dbt: staging resolves duplicates and corrections, the snapshot keeps history,
and freshness fails on a stale extract."""

import shutil
from pathlib import Path

import duckdb
import pytest
from conftest import query as _query

from loyalty import transform


def test_duplicate_deliveries_are_kept_once(after_batches) -> None:
    _, warehouse = after_batches
    rows = _query(
        warehouse,
        "select transaction_id, count(*) from staging.stg_pos__transactions "
        "where transaction_id in ('T9000001', 'T9000002') group by 1 order by 1",
    )
    assert rows == [("T9000001", 1), ("T9000002", 1)]  # D1
    lines = _query(
        warehouse, "select count(*) from staging.stg_pos__transaction_lines where transaction_id = 'T9000001'"
    )
    assert lines == [(1,)]
    events = _query(
        warehouse,
        "select count(*) from staging.stg_loyalty__events where event_id in ('E900000001', 'E900000002')",
    )
    assert events == [(2,)]  # D2


def test_a_late_correction_replaces_the_earlier_version(after_batches) -> None:
    after_1, after_2 = after_batches
    claim = "select member_id from staging.stg_pos__transactions where transaction_id = 'T9000014'"
    assert _query(after_1, claim) == [(None,)]
    assert _query(after_2, claim) == [("M90003",)]  # D11
    enrolled = (
        "select cast(enrolled_date as varchar) from staging.stg_loyalty__members where member_id = 'M90004'"
    )
    assert _query(after_1, enrolled) == [("2025-03-10",)]
    assert _query(after_2, enrolled) == [("2025-02-24",)]  # D12


def test_the_snapshot_keeps_what_was_reported_before_a_correction(after_batches) -> None:
    _, warehouse = after_batches
    versions = _query(
        warehouse,
        "select cast(enrolled_date as varchar), dbt_valid_to is null from snapshots.snp_member "
        "where member_id = 'M90004' order by dbt_valid_from",
    )
    assert versions == [("2025-03-10", False), ("2025-02-24", True)]  # A12
    # Members that were never corrected have exactly one version.
    multi = _query(
        warehouse,
        "select member_id from snapshots.snp_member group by 1 having count(*) > 1",
    )
    assert multi == [("M90004",)]


def test_late_reference_data_arrives_with_batch_2(after_batches) -> None:
    after_1, after_2 = after_batches
    product = "select count(*) from staging.stg_reference__products where product_id = 'P061'"
    assert _query(after_1, product) == [(0,)] and _query(after_2, product) == [(1,)]  # D10
    versions = "select count(*) from staging.stg_reference__stores"
    assert _query(after_1, versions) == [(14,)] and _query(after_2, versions) == [(15,)]


def test_a_stale_extract_fails_freshness(after_batches, tmp_path: Path) -> None:
    _, warehouse = after_batches
    stale = Path(shutil.copy(warehouse, tmp_path / "loyalty.duckdb"))
    transform.run_dbt(["source", "freshness"], stale)
    con = duckdb.connect(str(stale))
    con.execute("update raw.pos_transactions set _loaded_at = _loaded_at - interval 3 day")
    con.close()
    with pytest.raises(transform.DbtError):  # A13: 72 hours is past the 50-hour error threshold
        transform.run_dbt(["source", "freshness", "--select", "source:pos.transactions"], stale)
