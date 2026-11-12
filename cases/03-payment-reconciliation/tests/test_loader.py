"""Landing: what reaches raw, what is quarantined, and what is rejected outright. Each test gets a fresh database."""

import json
from pathlib import Path

import psycopg
import pytest
from conftest import write_arrival

from payrecon import loader
from payrecon.config import arrival_days

pytestmark = pytest.mark.db

EVENT = {
    "event_id": "evt-1",
    "payment_id": "pay-1",
    "merchant_id": "M001",
    "event_type": "initiated",
    "sequence_no": 1,
    "event_time": "2026-10-01 10:00:00",
    "ingestion_time": "2026-10-01 10:00:05",
    "amount_minor": 2500,
    "currency": "GBP",
}
LINE = {
    "settlement_line_id": "stl-1",
    "settlement_date": "2026-10-01",
    "payment_reference": "pay-1",
    "line_type": "sale",
    "currency": "GBP",
    "gross_minor": 2500,
    "fee_minor": 60,
    "net_minor": 2440,
}


def _count(con: psycopg.Connection, table: str) -> int:
    return con.execute(f"select count(*) from {table}").fetchone()[0]


def test_a_malformed_row_is_quarantined_and_the_rest_of_the_file_lands(con, tmp_path: Path) -> None:
    bad_amount = dict(EVENT, event_id="evt-2", amount_minor="12,50")
    bad_type = dict(EVENT, event_id="evt-3", event_type="settled")
    bad_net = dict(LINE, settlement_line_id="stl-2", net_minor=2500)
    folder = write_arrival(
        tmp_path / "a",
        "2026-10-02",
        {
            "events_2026-10-01.csv": [EVENT, bad_amount, bad_type],
            "settlement_GBP_2026-10-01.csv": [LINE, bad_net],
        },
    )
    results = loader.load_arrival(con, folder)
    assert [(r.loaded, r.quarantined) for r in results] == [(1, 2), (1, 1)]
    assert _count(con, "raw.payment_event") == 1
    assert _count(con, "raw.settlement_line") == 1
    reasons = dict(
        con.execute(
            "select raw_row->>'event_id', reason from ops.quarantine where raw_row ? 'event_id'"
        ).fetchall()
    )
    assert reasons == {
        "evt-2": "amount_minor is not an integer: '12,50'",
        "evt-3": "event_type 'settled' is not one of " + ", ".join(loader.EVENT_TYPES),
    }
    # Quarantine keeps the row as delivered, with its position, so it can be inspected and reprocessed.
    assert con.execute(
        "select row_number, stage from ops.quarantine where raw_row->>'event_id' = 'evt-2'"
    ).fetchone() == (2, "load")


def test_a_file_sent_again_lands_nothing(con, tmp_path: Path) -> None:
    files = {"settlement_GBP_2026-10-01.csv": [LINE]}
    loader.load_arrival(con, write_arrival(tmp_path / "first", "2026-10-02", files))
    results = loader.load_arrival(con, write_arrival(tmp_path / "again", "2026-10-04", files))
    assert [r.skipped for r in results] == [True]
    assert _count(con, "raw.settlement_line") == 1
    assert _count(con, "ops.file") == 1


def test_a_corrected_file_with_the_same_name_does_land(con, tmp_path: Path) -> None:
    """Skipping is by name and checksum, not name alone: a corrected re-issue is new content and must land.
    Deciding which version wins is core's job, not landing's."""
    loader.load_arrival(
        con, write_arrival(tmp_path / "first", "2026-10-02", {"settlement_GBP_2026-10-01.csv": [LINE]})
    )
    corrected = dict(LINE, fee_minor=50, net_minor=2450)
    results = loader.load_arrival(
        con, write_arrival(tmp_path / "fix", "2026-10-03", {"settlement_GBP_2026-10-01.csv": [corrected]})
    )
    assert [r.skipped for r in results] == [False]
    assert _count(con, "raw.settlement_line") == 2


@pytest.mark.parametrize("damage", ["checksum", "header", "missing"])
def test_a_damaged_arrival_is_rejected_whole(con, tmp_path: Path, damage: str) -> None:
    folder = write_arrival(
        tmp_path / "a",
        "2026-10-02",
        {
            "events_2026-10-01.csv": [EVENT],
            "settlement_GBP_2026-10-01.csv": [LINE],
        },
    )
    target = folder / "settlement_GBP_2026-10-01.csv"
    if damage == "checksum":
        target.write_text(target.read_text() + target.read_text().splitlines()[1] + "\n")
    elif damage == "header":
        target.write_text(target.read_text().replace("fee_minor", "fee"))
    else:
        target.unlink()
    with pytest.raises(loader.LoadError):
        loader.load_arrival(con, folder)
    # The events file was valid, but nothing from the arrival lands: it is all or nothing.
    assert _count(con, "ops.file") == 0
    assert _count(con, "raw.payment_event") == 0


def test_a_row_count_that_disagrees_with_the_manifest_rolls_back_the_arrival(con, tmp_path: Path) -> None:
    """The second file is checked only after the first has been written; the transaction must undo the first."""
    folder = write_arrival(
        tmp_path / "a",
        "2026-10-02",
        {
            "events_2026-10-01.csv": [EVENT],
            "settlement_GBP_2026-10-01.csv": [LINE],
        },
    )
    manifest = folder / "manifest.json"
    content = json.loads(manifest.read_text())
    content["files"][1]["rows"] = 2
    manifest.write_text(json.dumps(content))
    with pytest.raises(loader.LoadError, match="manifest says 2"):
        loader.load_arrival(con, folder)
    assert _count(con, "ops.file") == 0
    assert _count(con, "raw.payment_event") == 0


def test_the_full_delivery_lands_once(con, generated: Path) -> None:
    """Every generated arrival in order, then all of them again: the second pass lands nothing (rerun safety)."""
    for day in arrival_days():
        loader.load_arrival(con, generated / f"arrival_{day}")
    counts = {
        t: _count(con, t)
        for t in ("raw.merchant", "raw.payment_event", "raw.settlement_line", "ops.file", "ops.quarantine")
    }
    for day in arrival_days():
        assert all(r.skipped for r in loader.load_arrival(con, generated / f"arrival_{day}"))
    assert counts == {t: _count(con, t) for t in counts}
    assert counts["raw.merchant"] == 41  # 40 at the start, M041 late (P5)
    assert counts["ops.quarantine"] == 1  # only the planted malformed amount (P4)
    # The planted duplicate event lands twice: raw keeps what was delivered, core deduplicates by event_id (P1).
    assert (
        con.execute("select count(*) from raw.payment_event where event_id = 'evt-900000103'").fetchone()[0]
        == 2
    )
    # The resent file (P12) is skipped, so every settlement line id appears once.
    assert (
        con.execute(
            "select count(*) - count(distinct settlement_line_id) from raw.settlement_line"
        ).fetchone()[0]
        == 0
    )
