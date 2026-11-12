"""The generator is deterministic, plants each defect where stated, and is otherwise clean."""

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from conftest import read_rows

from loyalty import generator
from loyalty.config import SOURCE_COLUMNS
from loyalty.scenarios import SCENARIOS


def _checksums(out: Path) -> dict[str, str]:
    sums = {}
    for manifest in sorted(out.glob("batch_*/manifest.json")):
        for entry in json.loads(manifest.read_text())["files"]:
            sums[f"{manifest.parent.name}/{entry['file']}"] = entry["sha256"]
    return sums


def _both(generated: Path, table: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return read_rows(generated / "batch_1", table), read_rows(generated / "batch_2", table)


def test_same_seed_gives_identical_files(generated: Path, tmp_path: Path) -> None:
    generator.generate(tmp_path)
    assert _checksums(tmp_path) == _checksums(generated)


def test_a_different_seed_gives_different_files(generated: Path, tmp_path: Path) -> None:
    generator.generate(tmp_path, seed=1)
    assert _checksums(tmp_path) != _checksums(generated)


def test_every_scenario_record_is_planted(generated: Path) -> None:
    text = "".join(path.read_text(encoding="utf-8") for path in sorted(generated.glob("batch_*/*.csv")))
    missing = [(s.code, r) for s in SCENARIOS for r in s.records if r not in text]
    assert not missing


def test_duplicate_deliveries(generated: Path) -> None:
    b1, b2 = _both(generated, "pos_transactions")
    assert Counter(r["transaction_id"] for r in b1)["T9000001"] == 2  # D1, inside one file
    assert [r["transaction_id"] for r in b2].count("T9000002") == 1  # D1, across batches
    e1, e2 = _both(generated, "loyalty_events")
    assert Counter(r["event_id"] for r in e1)["E900000001"] == 2  # D2
    assert "E900000002" in {r["event_id"] for r in e1} and "E900000002" in {r["event_id"] for r in e2}


def test_late_corrections_arrive_in_batch_2(generated: Path) -> None:
    b1, b2 = _both(generated, "pos_transactions")
    first = next(r for r in b1 if r["transaction_id"] == "T9000014")
    claimed = next(r for r in b2 if r["transaction_id"] == "T9000014")
    assert first["member_id"] == "" and claimed["member_id"] == "M90003"  # D11
    assert claimed["updated_at"] > first["updated_at"]
    lines_2 = read_rows(generated / "batch_2", "pos_transaction_lines")
    assert "T9000014" not in {r["transaction_id"] for r in lines_2}  # header only: lines are unchanged

    m1, m2 = _both(generated, "loyalty_members")
    assert next(r for r in m1 if r["member_id"] == "M90004")["enrolled_at"].startswith("2025-03-10")
    assert next(r for r in m2 if r["member_id"] == "M90004")["enrolled_at"].startswith("2025-02-24")  # D12


def test_late_reference_data(generated: Path) -> None:
    p1, p2 = _both(generated, "reference_products")
    assert "P061" not in {r["product_id"] for r in p1} and "P061" in {r["product_id"] for r in p2}  # D10
    lines_1 = read_rows(generated / "batch_1", "pos_transaction_lines")
    assert any(r["product_id"] == "P061" for r in lines_1)


def test_status_column_disagreements(generated: Path) -> None:
    ents = {r["entitlement_id"]: r for r in read_rows(generated / "batch_1", "loyalty_reward_entitlements")}
    events = read_rows(generated / "batch_1", "loyalty_events")
    redeemed = {r["entitlement_id"] for r in events if r["event_type"] == "reward_redeemed"}
    assert ents["R9000002"]["status"] == "issued" and "R9000002" in redeemed  # D13
    assert ents["R9000003"]["status"] == "issued" and ents["R9000003"]["expires_at"] < "2025-12-02"  # D14


@pytest.mark.parametrize("batch", [1, 2])
def test_headers_match_the_contract(generated: Path, batch: int) -> None:
    for table, columns in SOURCE_COLUMNS.items():
        header = (generated / f"batch_{batch}" / f"{table}.csv").read_text(encoding="utf-8").splitlines()[0]
        assert header.split(",") == columns


# The background population must be clean, so that any anomaly found by the models is either planted or a bug.


def _all(generated: Path, table: str) -> list[dict[str, str]]:
    b1, b2 = _both(generated, table)
    return b1 + b2


def _natural(row: dict[str, str], key: str) -> bool:
    return not row[key].startswith(("T9", "E9", "R9", "M9"))


def test_background_lines_have_transactions_and_returns_have_sales(generated: Path) -> None:
    txns = {r["transaction_id"]: r for r in _all(generated, "pos_transactions")}
    lines = _all(generated, "pos_transaction_lines")
    assert all(r["transaction_id"] in txns for r in lines)

    sold = {
        (r["transaction_id"], r["line_number"]): int(r["quantity"]) for r in lines if int(r["quantity"]) > 0
    }
    returned: Counter[tuple[str, str]] = Counter()
    for line in lines:
        if int(line["quantity"]) < 0 and _natural(line, "transaction_id"):
            original = txns[line["transaction_id"]]["original_transaction_id"]
            key = (original, line["original_line_number"])
            assert key in sold, key
            returned[key] -= int(line["quantity"])
    assert all(returned[key] <= sold[key] for key in returned)


def test_background_sales_happen_at_open_stores_after_enrolment(generated: Path) -> None:
    opened = {r["store_id"]: r["opened_date"] for r in _all(generated, "reference_stores")}
    members = {r["member_id"]: r["enrolled_at"] for r in _all(generated, "loyalty_members")}
    for txn in _all(generated, "pos_transactions"):
        if not _natural(txn, "transaction_id"):
            continue
        assert txn["transaction_at"][:10] >= opened[txn["store_id"]]
        if txn["member_id"] and txn["transaction_type"] == "sale":
            assert txn["transaction_at"] > members[txn["member_id"]]


def test_background_reward_status_agrees_with_the_event_log(generated: Path) -> None:
    redeemed = {
        r["entitlement_id"] for r in _all(generated, "loyalty_events") if r["event_type"] == "reward_redeemed"
    }
    latest: dict[str, dict[str, str]] = {}
    for row in _all(generated, "loyalty_reward_entitlements"):  # batch 2 rows come later and win
        latest[row["entitlement_id"]] = row
    for ent_id, row in latest.items():
        if _natural(row, "entitlement_id"):
            assert (row["status"] == "redeemed") == (ent_id in redeemed), ent_id


def test_observation_period_bounds(generated: Path) -> None:
    dates = [r["transaction_at"][:10] for r in _all(generated, "pos_transactions")]
    assert min(dates) >= date(2024, 10, 1).isoformat() and max(dates) <= date(2026, 9, 30).isoformat()
