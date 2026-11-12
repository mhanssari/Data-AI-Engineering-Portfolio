"""Core build: distinct events, payment state by sequence, illegal transitions, reference-data lag, settlement lines.

Most tests read the full generated delivery (`delivered`), rebuilt as of different arrival dates. Behaviours the
generator does not plant (a corrected settlement file, an over-refund, a broken sequence contract) use small
hand-written arrivals in a fresh database (`con`).
"""

from collections import defaultdict
from datetime import date
from pathlib import Path

import psycopg
import pytest
from conftest import TRANSITIONS, write_arrival

from payrecon import core, generator, loader

pytestmark = pytest.mark.db

LAST_ARRIVAL = date(2026, 11, 1)


def _one(con: psycopg.Connection, sql: str, params=None):
    return con.execute(sql, params).fetchone()


def _fingerprint(con: psycopg.Connection) -> dict[str, str]:
    """An order-independent digest of every core table's content."""
    return {
        table: _one(
            con, f"select md5(coalesce(string_agg(t::text, '|' order by t::text), '')) from {table} t"
        )[0]
        for table in core.TABLES
    }


def test_the_state_machine_table_matches_the_documented_one(delivered) -> None:
    rows = delivered.execute(
        "select from_status, event_type, to_status from core.allowed_transition"
    ).fetchall()
    assert {(f, e): t for f, e, t in rows} == TRANSITIONS


def test_each_distinct_event_is_counted_once(delivered) -> None:
    """Invariant 1 and planted P1: distinct events = landed rows - repeated deliveries."""
    core.build(delivered, LAST_ARRIVAL)
    landed = _one(delivered, "select count(*) from raw.payment_event")[0]
    distinct = _one(delivered, "select count(*) from core.payment_event")[0]
    repeats = _one(delivered, "select coalesce(sum(delivery_count - 1), 0) from core.payment_event")[0]
    assert repeats == 1
    assert distinct == landed - repeats
    assert _one(
        delivered, "select delivery_count from core.payment_event where event_id = 'evt-900000103'"
    ) == (2,)


def test_every_payment_state_matches_an_independent_replay(delivered) -> None:
    """Invariant 2: replay each payment's events from the generator, in sequence order, through the documented
    transitions in plain Python, and compare with core.payment for every payment."""
    core.build(delivered, LAST_ARRIVAL)
    by_payment = defaultdict(list)
    for e in generator.build_world().events:
        if isinstance(e.amount_minor, int):  # the malformed P4 event never lands
            by_payment[e.payment_id].append(e)
    expected = {}
    for payment_id, events in by_payment.items():
        status, captured, refunded, reversed_ = "new", 0, 0, 0
        for e in sorted(events, key=lambda e: e.sequence_no):
            to = TRANSITIONS.get((status, e.event_type))
            if to is None or (e.event_type == "refunded" and e.amount_minor > captured - refunded):
                continue
            status = to
            captured = e.amount_minor if e.event_type == "captured" else captured
            refunded += e.amount_minor if e.event_type == "refunded" else 0
            reversed_ = captured - refunded if e.event_type == "reversed" else reversed_
        expected[payment_id] = (status, captured, refunded, reversed_, captured - refunded - reversed_)
    actual = {
        row[0]: tuple(row[1:])
        for row in delivered.execute(
            "select payment_id, status, captured_minor, refunded_minor, reversed_minor, expected_settlement_minor "
            "from core.payment"
        )
    }
    assert actual == expected


def test_core_is_referentially_whole(delivered) -> None:
    """Core has no foreign keys (decision 0004: they cost most of the build), so their guarantees are checked here."""
    core.build(delivered, LAST_ARRIVAL)
    orphans = {
        "event without merchant": "select count(*) from core.payment_event e "
        "where not exists (select 1 from core.merchant m where m.merchant_id = e.merchant_id)",
        "event without payment": "select count(*) from core.payment_event e "
        "where not exists (select 1 from core.payment p where p.payment_id = e.payment_id)",
        "payment without merchant": "select count(*) from core.payment p "
        "where not exists (select 1 from core.merchant m where m.merchant_id = p.merchant_id)",
        "illegal transition without event": "select count(*) from core.illegal_transition i "
        "where not exists (select 1 from core.payment_event e where e.event_id = i.event_id)",
        "row without its file": "select count(*) from (select file_id from core.payment_event union all "
        "select file_id from core.settlement_line union all select file_id from core.merchant) c "
        "where not exists (select 1 from ops.file f where f.file_id = c.file_id)",
    }
    assert {name: _one(delivered, sql)[0] for name, sql in orphans.items()} == dict.fromkeys(orphans, 0)


def test_out_of_order_events_wait_for_the_missing_one(delivered) -> None:
    """A2, planted P2: captured (sequence 3) arrives a day before authorised (sequence 2). It is held, not applied
    and not illegal, until sequence 2 arrives; then the final state is captured."""
    core.build(delivered, date(2026, 10, 4))
    assert _one(
        delivered,
        "select status, last_sequence_no, held_events from core.payment where payment_id = 'pay-9000002'",
    ) == ("initiated", 1, 1)
    core.build(delivered, date(2026, 10, 5))
    assert _one(
        delivered,
        "select status, last_sequence_no, held_events from core.payment where payment_id = 'pay-9000002'",
    ) == ("captured", 3, 0)
    assert _one(
        delivered, "select count(*) from core.illegal_transition where payment_id = 'pay-9000002'"
    ) == (0,)


def test_an_illegal_transition_is_recorded_and_not_applied(delivered) -> None:
    """A3, planted P3: a refund on a payment never captured; the later void still applies."""
    core.build(delivered, LAST_ARRIVAL)
    assert delivered.execute(
        "select event_id, from_status, event_type from core.illegal_transition"
    ).fetchall() == [("evt-900000303", "authorised", "refunded")]
    assert _one(
        delivered, "select status, refunded_minor from core.payment where payment_id = 'pay-9000003'"
    ) == ("voided", 0)


def test_events_for_an_unknown_merchant_wait_in_quarantine_until_it_arrives(delivered) -> None:
    """A4, planted P5: M041's events arrive on 10 October; the merchant on 12 October."""
    open_core_quarantine = (
        "select count(*) from ops.quarantine where stage = 'core' and reprocessed_at is null "
        "and raw_row->>'payment_id' = 'pay-9000005'"
    )
    core.build(delivered, date(2026, 10, 11))
    assert _one(delivered, "select count(*) from core.payment where payment_id = 'pay-9000005'") == (0,)
    assert _one(delivered, open_core_quarantine) == (3,)
    core.build(delivered, date(2026, 10, 12))
    assert _one(delivered, "select status from core.payment where payment_id = 'pay-9000005'") == (
        "captured",
    )
    assert _one(delivered, open_core_quarantine) == (0,)
    # Rebuilding the earlier day does not quarantine the same rows a second time.
    core.build(delivered, date(2026, 10, 11))
    assert _one(delivered, "select count(*) from ops.quarantine where stage = 'core'") == (3,)


def test_rebuilding_a_day_gives_identical_core(delivered) -> None:
    """A7 and invariant 8: core as of a date depends only on what had arrived by then, even after later loads."""
    core.build(delivered, date(2026, 10, 10))
    first = _fingerprint(delivered)
    core.build(delivered, LAST_ARRIVAL)
    assert _fingerprint(delivered) != first
    core.build(delivered, date(2026, 10, 10))
    assert _fingerprint(delivered) == first


# ------------------------------------------------------------------------------------------- hand-written arrivals

MERCHANT = {
    "merchant_id": "M001",
    "merchant_name": "Merchant 001",
    "country": "GB",
    "currency": "GBP",
    "fee_bps": 150,
    "fee_fixed_minor": 20,
    "updated_at": "2026-09-30 00:00:00",
}


def _event(seq: int, event_type: str, amount: int = 5000, payment_id: str = "pay-1") -> dict:
    return {
        "event_id": f"evt-{payment_id}-{seq}",
        "payment_id": payment_id,
        "merchant_id": "M001",
        "event_type": event_type,
        "sequence_no": seq,
        "event_time": f"2026-10-01 10:0{seq}:00",
        "ingestion_time": f"2026-10-01 10:0{seq}:30",
        "amount_minor": amount,
        "currency": "GBP",
    }


def _line(fee: int) -> dict:
    return {
        "settlement_line_id": "stl-1",
        "settlement_date": "2026-10-01",
        "payment_reference": "pay-1",
        "line_type": "sale",
        "currency": "GBP",
        "gross_minor": 5000,
        "fee_minor": fee,
        "net_minor": 5000 - fee,
    }


def test_a_refund_larger_than_what_remains_is_not_applied(con, tmp_path: Path) -> None:
    events = [
        _event(1, "initiated"),
        _event(2, "authorised"),
        _event(3, "captured"),
        _event(4, "refunded", 3000),
        _event(5, "refunded", 2500),
    ]
    loader.load_arrival(
        con,
        write_arrival(
            tmp_path / "a", "2026-10-02", {"merchants.csv": [MERCHANT], "events_2026-10-01.csv": events}
        ),
    )
    core.build(con, date(2026, 10, 2))
    assert _one(con, "select status, refunded_minor, expected_settlement_minor from core.payment") == (
        "refunded",
        3000,
        2000,
    )
    assert _one(con, "select event_id, reason from core.illegal_transition") == (
        "evt-pay-1-5",
        "refund of 2500 exceeds the 2000 remaining",
    )


def test_a_corrected_settlement_line_supersedes_the_original(con, tmp_path: Path) -> None:
    name = "settlement_GBP_2026-10-01.csv"
    loader.load_arrival(con, write_arrival(tmp_path / "a", "2026-10-02", {name: [_line(120)]}))
    loader.load_arrival(con, write_arrival(tmp_path / "b", "2026-10-03", {name: [_line(95)]}))
    core.build(con, date(2026, 10, 2))
    assert _one(con, "select fee_minor, delivery_count from core.settlement_line") == (120, 1)
    core.build(con, date(2026, 10, 3))
    assert _one(con, "select fee_minor, delivery_count from core.settlement_line") == (95, 2)


def test_two_events_claiming_one_sequence_number_stop_the_build(con, tmp_path: Path) -> None:
    """The sequence number is the ordering contract; a conflict is not guessed at. Nothing is half-built."""
    clash = dict(_event(2, "declined"), event_id="evt-other")
    loader.load_arrival(
        con,
        write_arrival(
            tmp_path / "a",
            "2026-10-02",
            {
                "merchants.csv": [MERCHANT],
                "events_2026-10-01.csv": [_event(1, "initiated"), _event(2, "authorised"), clash],
            },
        ),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        core.build(con, date(2026, 10, 2))
    assert _one(con, "select count(*) from core.build") == (0,)
