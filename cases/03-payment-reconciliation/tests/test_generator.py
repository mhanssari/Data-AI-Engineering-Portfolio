"""The generator is the test oracle for everything downstream, so it is tested first.

Three properties matter: the same seed gives byte-identical files; every planted defect P1-P16 is present; and the
random background is clean, so any exception a later stage raises can only come from a planted record.
"""

import csv
import json
from collections import defaultdict
from pathlib import Path

import pytest

from payrecon import generator
from payrecon.config import arrival_days
from payrecon.scenarios import SCENARIOS

# The state machine in docs/data-model.md: allowed (previous type, next type) pairs.
LEGAL = {
    (None, "initiated"),
    ("initiated", "authorised"),
    ("initiated", "declined"),
    ("authorised", "captured"),
    ("authorised", "voided"),
    ("authorised", "expired"),
    ("captured", "refunded"),
    ("refunded", "refunded"),
    ("captured", "reversed"),
}


def _rows(base: Path, pattern: str) -> list[dict]:
    rows = []
    for path in sorted(base.glob(f"arrival_*/{pattern}")):
        with path.open(newline="", encoding="utf-8") as handle:
            rows.extend(
                dict(row, _file=path.name, _arrival=path.parent.name) for row in csv.DictReader(handle)
            )
    return rows


@pytest.fixture(scope="module")
def world() -> generator.World:
    return generator.build_world()


def test_same_seed_gives_identical_files(generated: Path, tmp_path: Path) -> None:
    generator.generate(tmp_path)
    first = {p.parent.name: json.loads(p.read_text()) for p in generated.glob("arrival_*/manifest.json")}
    second = {p.parent.name: json.loads(p.read_text()) for p in tmp_path.glob("arrival_*/manifest.json")}
    assert first == second  # the manifests carry every file's sha256


def test_one_folder_per_arrival_day(generated: Path) -> None:
    assert sorted(p.name for p in generated.glob("arrival_*")) == [f"arrival_{d}" for d in arrival_days()]


def test_every_planted_record_is_delivered(generated: Path) -> None:
    delivered = set()
    for row in _rows(generated, "*.csv"):
        delivered.update(
            v
            for k, v in row.items()
            if k
            in ("event_id", "payment_id", "merchant_id", "payment_reference", "settlement_line_id", "_file")
        )
    for scenario in SCENARIOS:
        missing = [record for record in scenario.records if record not in delivered]
        assert not missing, f"{scenario.code}: {missing} not delivered"


def test_planted_delivery_defects(generated: Path) -> None:
    events = _rows(generated, "events_*.csv")
    assert sum(e["event_id"] == "evt-900000103" for e in events) == 2  # P1
    assert [e["amount_minor"] for e in events if e["event_id"] == "evt-900000401"] == ["12,50"]  # P4
    p2 = {e["sequence_no"]: e["_arrival"] for e in events if e["payment_id"] == "pay-9000002"}
    assert p2["3"] < p2["2"]  # P2: captured lands a day before authorised
    m041 = [r["_arrival"] for r in _rows(generated, "merchants*.csv") if r["merchant_id"] == "M041"]
    assert m041 == ["arrival_2026-10-12"]  # P5
    resent = sorted(p.parent.name for p in generated.glob("arrival_*/settlement_GBP_2026-10-13.csv"))
    assert resent == ["arrival_2026-10-14", "arrival_2026-10-15"]  # P12
    late = [p.parent.name for p in generated.glob("arrival_*/settlement_USD_2026-10-20.csv")]
    assert late == ["arrival_2026-10-23"]  # P14


def test_background_follows_the_state_machine(world: generator.World) -> None:
    by_payment = defaultdict(list)
    for e in world.events:
        if not e.payment_id.startswith("pay-9"):
            by_payment[e.payment_id].append(e)
    assert len(by_payment) > 50_000
    for payment_id, events in by_payment.items():
        events.sort(key=lambda e: e.sequence_no)
        assert [e.sequence_no for e in events] == list(range(1, len(events) + 1)), payment_id
        previous = None
        for e in events:
            assert (previous, e.event_type) in LEGAL, f"{payment_id}: {previous} -> {e.event_type}"
            previous = e.event_type


def test_background_settles_exactly_what_was_captured(world: generator.World) -> None:
    """Every background sale line matches one capture at the plan's fee; nothing captured on or before the last
    settlement day is left unsettled. Planted payments (pay-9...) are excluded: they are the deliberate exceptions."""
    merchants = {m.merchant_id: m for m in world.merchants}
    captures = {
        e.payment_id: e
        for e in world.events
        if e.event_type == "captured" and not e.payment_id.startswith("pay-9")
    }
    sales = defaultdict(list)
    for line in world.lines:
        if not line.payment_reference.startswith("pay-9") and line.line_type == "sale":
            sales[line.payment_reference].append(line)
    assert set(sales) == set(captures)
    for payment_id, lines in sales.items():
        capture = captures[payment_id]
        merchant = merchants[capture.merchant_id]
        assert len(lines) == 1, payment_id
        (line,) = lines
        assert (line.gross_minor, line.currency, line.settlement_date) == (
            capture.amount_minor,
            capture.currency,
            capture.event_time.date(),
        ), payment_id
        assert line.fee_minor == merchant.fee(line.gross_minor), payment_id


def test_background_never_refunds_more_than_was_captured(world: generator.World) -> None:
    net = defaultdict(int)
    for line in world.lines:
        net[line.payment_reference] += line.gross_minor
    assert all(amount >= 0 for amount in net.values())


def test_fee_rounds_half_up_in_minor_units() -> None:
    merchant = generator.Merchant("M999", "GBP", 150, 20, None)
    assert merchant.fee(1000) == 15 + 20  # exactly 15.0
    assert merchant.fee(1030) == 15 + 20  # 15.45 rounds down
    assert merchant.fee(1100) == 17 + 20  # 16.5 rounds half up
