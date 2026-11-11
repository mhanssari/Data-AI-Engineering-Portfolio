"""Landing is append-only, idempotent, ordered and all-or-nothing."""

import json
import shutil
from pathlib import Path

import duckdb
import pytest

from loyalty import loader


@pytest.fixture
def con(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    connection = loader.connect(tmp_path / "warehouse.duckdb")
    yield connection
    connection.close()


def _manifest(batch_dir: Path) -> dict:
    return json.loads((batch_dir / "manifest.json").read_text())


def _raw_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    tables = [
        r[0]
        for r in con.execute(
            "select table_name from information_schema.tables where table_schema = 'raw'"
        ).fetchall()
    ]
    return {t: con.execute(f"select count(*) from raw.{t}").fetchone()[0] for t in tables}


def test_a_batch_lands_every_row_of_its_manifest(con, generated: Path) -> None:
    batch = generated / "batch_1"
    results = loader.load_batch(con, batch)
    expected = {e["table"]: e["rows"] for e in _manifest(batch)["files"]}
    assert {r.table: r.rows for r in results} == expected
    counts = _raw_counts(con)
    assert all(counts[table] == rows for table, rows in expected.items())


def test_loading_the_same_batch_twice_lands_nothing(con, generated: Path) -> None:
    loader.load_batch(con, generated / "batch_1")
    before = _raw_counts(con)
    again = loader.load_batch(con, generated / "batch_1")
    assert all(r.skipped and r.rows == 0 for r in again)
    assert _raw_counts(con) == before


def test_raw_is_append_only_across_batches(con, generated: Path) -> None:
    loader.load_batch(con, generated / "batch_1")
    loader.load_batch(con, generated / "batch_2")
    # Batch 2 re-sends every store version; raw keeps both deliveries, staging deduplicates.
    by_batch = con.execute(
        "select _batch_id, count(*) from raw.reference_stores group by 1 order by 1"
    ).fetchall()
    assert by_batch == [(1, 14), (2, 15)]
    load_ids = con.execute("select _load_id from raw.pos_transactions group by 1 order by 1").fetchall()
    assert len(load_ids) == 2  # one load id per file, so a watermark can tell the batches apart


def test_batches_must_arrive_in_order(con, generated: Path) -> None:
    with pytest.raises(loader.LoadError, match="must arrive in order"):
        loader.load_batch(con, generated / "batch_2")
    assert sum(_raw_counts(con).values()) == 0


def test_a_changed_file_is_rejected_and_nothing_lands(con, generated: Path, tmp_path: Path) -> None:
    batch = shutil.copytree(generated / "batch_1", tmp_path / "batch_1")
    lines = batch / "pos_transaction_lines.csv"
    lines.write_text(
        lines.read_text(encoding="utf-8") + "T0000001,99,P001,1,1.00,0.00,GBP,\n", encoding="utf-8"
    )
    with pytest.raises(loader.LoadError, match="checksum"):
        loader.load_batch(con, batch)
    assert sum(_raw_counts(con).values()) == 0


def test_a_column_change_is_rejected_and_nothing_lands(con, generated: Path, tmp_path: Path) -> None:
    batch = shutil.copytree(generated / "batch_1", tmp_path / "batch_1")
    members = batch / "loyalty_members.csv"
    members.write_text(
        members.read_text(encoding="utf-8").replace("birth_month", "birth_date", 1), encoding="utf-8"
    )
    # Re-sign the manifest, so the contract check is what rejects the file, not the checksum.
    manifest = _manifest(batch)
    for entry in manifest["files"]:
        if entry["table"] == "loyalty_members":
            entry["sha256"] = loader._sha256(members)
    (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(loader.LoadError, match="do not match the contract"):
        loader.load_batch(con, batch)
    assert sum(_raw_counts(con).values()) == 0


def test_a_failure_part_way_through_rolls_back_the_whole_batch(con, generated: Path, tmp_path: Path) -> None:
    batch = shutil.copytree(generated / "batch_1", tmp_path / "batch_1")
    # A manifest that understates one file's rows passes validation but fails after earlier files have landed.
    manifest = _manifest(batch)
    manifest["files"][-1]["rows"] -= 1
    (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(loader.LoadError, match="landed"):
        loader.load_batch(con, batch)
    counts = _raw_counts(con)
    assert sum(counts.values()) == 0, counts
