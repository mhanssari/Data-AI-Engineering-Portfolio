"""Seeded generator: a payment provider's lifecycle events, the acquirer's settlement files, and their delivery.

Every payment's lifecycle is simulated first; the acquirer then settles what was captured, refunded or reversed on
each day. Files are delivered into one folder per arrival day: arrival A holds the event file and the settlement
files for day A - 1. Same seed, same bytes: one random.Random consumed in a fixed order, ids assigned after a
stable sort. The planted defects in scenarios.py are added on top as explicit records.
"""

import csv
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from payrecon.config import (
    CURRENCIES,
    FILE_COLUMNS,
    FIRST_PAYMENT_DAY,
    LAST_PAYMENT_DAY,
    LAST_REPORTED_DAY,
    MERCHANT_COUNT,
    SEED,
)

COUNTRIES = {"GBP": "GB", "EUR": "DE", "USD": "US"}
PAYMENTS_PER_DAY = 2000


@dataclass
class Merchant:
    merchant_id: str
    currency: str
    fee_bps: int
    fee_fixed_minor: int
    updated_at: datetime

    def fee(self, gross_minor: int) -> int:
        """Basis points of the gross, rounded half up to a minor unit, plus the fixed fee (decision 0002)."""
        return (gross_minor * self.fee_bps + 5000) // 10000 + self.fee_fixed_minor


@dataclass
class Event:
    payment_id: str
    merchant_id: str
    event_type: str
    sequence_no: int
    event_time: datetime
    ingestion_time: datetime
    amount_minor: int | str  # a string only for the planted malformed row (P4)
    currency: str
    event_id: str = ""


@dataclass
class Line:
    settlement_date: date
    payment_reference: str
    line_type: str
    currency: str
    gross_minor: int
    fee_minor: int
    settlement_line_id: str = ""

    @property
    def net_minor(self) -> int:
        return self.gross_minor - self.fee_minor


@dataclass
class World:
    merchants: list[Merchant]
    late_merchants: list[Merchant]
    events: list[Event]
    lines: list[Line]
    duplicated_event_ids: set[str] = field(default_factory=set)
    # (file name, arrival day) overrides: a late file, and a file sent again
    late_files: dict[str, date] = field(default_factory=dict)
    resent_files: dict[str, date] = field(default_factory=dict)


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _at(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second))


# ------------------------------------------------------------------------------------------- random population


def _merchants(rng: random.Random) -> list[Merchant]:
    return [
        Merchant(
            f"M{n:03d}",
            CURRENCIES[(n - 1) % len(CURRENCIES)],
            rng.randint(120, 290),
            rng.choice([20, 25, 30]),
            _at(date(2026, 9, 30), 0),
        )
        for n in range(1, MERCHANT_COUNT + 1)
    ]


def _lifecycle(rng: random.Random, start: datetime) -> tuple[list, dict]:
    """Events of one payment as (type, event time, amount), and its settlement facts."""
    amount = max(100, min(500000, int(rng.lognormvariate(8, 0.8))))
    steps: list[tuple[str, datetime, int]] = [("initiated", start, amount)]
    facts: dict = {"captured_at": None, "captured": 0, "refunds": [], "reversed_at": None}
    t = start + timedelta(seconds=rng.randint(1, 30))
    if rng.random() < 0.08:
        steps.append(("declined", t, amount))
        return steps, facts
    steps.append(("authorised", t, amount))
    outcome = rng.random()
    if outcome < 0.85:
        t = t + timedelta(seconds=rng.randint(60, 14400))
        steps.append(("captured", t, amount))
        facts.update(captured_at=t, captured=amount)
        if rng.random() < 0.05:
            when = t + timedelta(days=rng.randint(1, 10), seconds=rng.randint(0, 36000))
            refund = amount if rng.random() < 0.4 else max(1, amount * rng.randint(10, 90) // 100)
            steps.append(("refunded", when, refund))
            facts["refunds"].append((when, refund))
        elif rng.random() < 0.005:
            when = t + timedelta(days=rng.randint(1, 5), seconds=rng.randint(0, 36000))
            steps.append(("reversed", when, amount))
            facts["reversed_at"] = when
    elif outcome < 0.90:
        steps.append(("voided", t + timedelta(seconds=rng.randint(60, 7200)), amount))
    else:
        steps.append(("expired", t + timedelta(days=7), amount))
    return steps, facts


def _population(rng: random.Random, merchants: list[Merchant]) -> tuple[list[Event], list[tuple]]:
    weights = [rng.random() + 0.3 for _ in merchants]
    drafts = []
    day = FIRST_PAYMENT_DAY
    while day <= LAST_PAYMENT_DAY:
        for _ in range(PAYMENTS_PER_DAY + rng.randint(-150, 150)):
            merchant = rng.choices(merchants, weights)[0]
            start = _at(day, 0) + timedelta(seconds=rng.randint(0, 86399))
            steps, facts = _lifecycle(rng, start)
            delays = [rng.randint(1, 90) for _ in steps]
            drafts.append((start, merchant, steps, facts, delays))
        day += timedelta(days=1)
    drafts.sort(key=lambda d: d[0])

    events, settled = [], []
    for number, (_, merchant, steps, facts, delays) in enumerate(drafts, start=1):
        payment_id = f"pay-{number:07d}"
        for seq, ((kind, when, amount), delay) in enumerate(zip(steps, delays, strict=True), start=1):
            # Background events are ingested the same day they happen, so a capture is never reported after the
            # acquirer settles it. Cross-day arrival is planted on purpose instead (P2).
            ingested = min(when + timedelta(seconds=delay), _at(when.date(), 23, 59, 59))
            events.append(
                Event(payment_id, merchant.merchant_id, kind, seq, when, ingested, amount, merchant.currency)
            )
        settled.append((payment_id, merchant, facts))
    return events, settled


def _settlement_lines(settled: list[tuple]) -> list[Line]:
    """What the acquirer settles: sales on the capture day, refunds and reversals on the day they happen."""
    lines = []
    for payment_id, merchant, facts in settled:
        if facts["captured_at"] is None:
            continue
        gross = facts["captured"]
        lines.append(
            Line(
                facts["captured_at"].date(), payment_id, "sale", merchant.currency, gross, merchant.fee(gross)
            )
        )
        refunded = 0
        for when, amount in facts["refunds"]:
            lines.append(Line(when.date(), payment_id, "refund", merchant.currency, -amount, 0))
            refunded += amount
        if facts["reversed_at"] is not None:
            lines.append(
                Line(
                    facts["reversed_at"].date(),
                    payment_id,
                    "reversal",
                    merchant.currency,
                    -(gross - refunded),
                    0,
                )
            )
    return [line for line in lines if line.settlement_date <= LAST_REPORTED_DAY]


# ------------------------------------------------------------------------------------------- planted defects


def _plant(world: World) -> None:
    by_id = {m.merchant_id: m for m in world.merchants}
    gbp, eur, usd = by_id["M001"], by_id["M002"], by_id["M003"]
    late = Merchant("M041", "GBP", 175, 25, _at(date(2026, 10, 11), 18))
    world.late_merchants.append(late)

    def payment(number: int, merchant: Merchant, steps: list[tuple], delays: dict | None = None) -> str:
        """steps: (type, event time, amount); delays: sequence -> ingestion delay, default 30 seconds."""
        payment_id = f"pay-{9000000 + number}"
        for seq, (kind, when, amount) in enumerate(steps, start=1):
            delay = (delays or {}).get(seq, timedelta(seconds=30))
            world.events.append(
                Event(
                    payment_id,
                    merchant.merchant_id,
                    kind,
                    seq,
                    when,
                    when + delay,
                    amount,
                    merchant.currency,
                    f"evt-{9000000 + number}{seq:02d}",
                )
            )
        return payment_id

    def sale(
        payment_id: str,
        merchant: Merchant,
        day: date,
        gross: int,
        fee: int | None = None,
        *,
        currency: str | None = None,
        line_id: str | None = None,
    ) -> None:
        world.lines.append(
            Line(
                day,
                payment_id,
                "sale",
                currency or merchant.currency,
                gross,
                merchant.fee(gross) if fee is None else fee,
                line_id or "",
            )
        )

    def captured(day: date, hour: int, amount: int) -> list[tuple]:
        start = _at(day, hour)
        return [
            ("initiated", start, amount),
            ("authorised", start + timedelta(seconds=5), amount),
            ("captured", start + timedelta(minutes=10), amount),
        ]

    # P1: the captured event delivered twice in the same file.
    p1 = payment(1, gbp, captured(date(2026, 10, 3), 10, 2500))
    sale(p1, gbp, date(2026, 10, 3), 2500)
    world.duplicated_event_ids.add("evt-900000103")

    # P2: authorised (sequence 2) arrives a day after captured (sequence 3).
    p2 = payment(2, eur, captured(date(2026, 10, 3), 11, 4200), delays={2: timedelta(days=1)})
    sale(p2, eur, date(2026, 10, 3), 4200)

    # P3: a refund for a payment that was never captured; then voided.
    start = _at(date(2026, 10, 5), 9)
    payment(
        3,
        usd,
        [
            ("initiated", start, 3000),
            ("authorised", start + timedelta(seconds=5), 3000),
            ("refunded", start + timedelta(hours=2), 3000),
            ("voided", start + timedelta(hours=3), 3000),
        ],
    )

    # P4: a malformed amount; the payment has only this event.
    payment(4, gbp, [("initiated", _at(date(2026, 10, 5), 12), "12,50")])

    # P5: events for a merchant that reference data only sends on 12 October.
    p5 = payment(5, late, captured(date(2026, 10, 9), 14, 3300))
    sale(p5, late, date(2026, 10, 9), 3300)

    # P6: captured, never settled.
    payment(6, gbp, captured(date(2026, 10, 6), 9, 5600))

    # P7: a settlement line for a payment that does not exist.
    world.lines.append(
        Line(date(2026, 10, 7), "pay-9000099", "sale", "EUR", 7700, eur.fee(7700), "stl-EUR-20261007-P7")
    )

    # P8: two partial lines that sum to the capture, fees split so they sum to the plan's fee.
    p8 = payment(8, eur, captured(date(2026, 10, 8), 10, 10000))
    fee = eur.fee(10000)
    sale(p8, eur, date(2026, 10, 8), 6000, fee // 2, line_id="stl-EUR-20261008-P8a")
    sale(p8, eur, date(2026, 10, 8), 4000, fee - fee // 2, line_id="stl-EUR-20261008-P8b")

    # P9: two partial lines 10.00 short of the capture.
    p9 = payment(9, usd, captured(date(2026, 10, 9), 10, 15000))
    fee = usd.fee(15000)
    sale(p9, usd, date(2026, 10, 9), 9000, fee // 2, line_id="stl-USD-20261009-P9a")
    sale(p9, usd, date(2026, 10, 9), 5000, fee - fee // 2, line_id="stl-USD-20261009-P9b")

    # P10: a fee 1.50 above the plan.
    p10 = payment(10, gbp, captured(date(2026, 10, 10), 10, 8000))
    sale(p10, gbp, date(2026, 10, 10), 8000, gbp.fee(8000) + 150)

    # P11: a GBP payment settled in the EUR file.
    p11 = payment(11, gbp, captured(date(2026, 10, 11), 10, 6100))
    sale(p11, gbp, date(2026, 10, 11), 6100, currency="EUR")

    # P12: the GBP file for 13 October sent again with the arrival of 15 October.
    world.resent_files["settlement_GBP_2026-10-13.csv"] = date(2026, 10, 15)

    # P13: the same payment settled on 13 and 14 October under two line ids.
    p13 = payment(13, eur, captured(date(2026, 10, 13), 10, 4400))
    sale(p13, eur, date(2026, 10, 13), 4400)
    sale(p13, eur, date(2026, 10, 14), 4400)

    # P14: the USD file for 20 October arrives on 23 October instead of 21 October.
    world.late_files["settlement_USD_2026-10-20.csv"] = date(2026, 10, 23)

    # P15: a refund after settlement, and a full reversal.
    start = _at(date(2026, 10, 2), 10)
    p15 = payment(
        15, gbp, captured(date(2026, 10, 2), 10, 10000) + [("refunded", start + timedelta(days=3), 3000)]
    )
    sale(p15, gbp, date(2026, 10, 2), 10000)
    world.lines.append(Line(date(2026, 10, 5), p15, "refund", "GBP", -3000, 0))
    start = _at(date(2026, 10, 3), 10)
    p16 = payment(
        16, gbp, captured(date(2026, 10, 3), 10, 4000) + [("reversed", start + timedelta(days=3), 4000)]
    )
    sale(p16, gbp, date(2026, 10, 3), 4000)
    world.lines.append(Line(date(2026, 10, 6), p16, "reversal", "GBP", -4000, 0))

    # P16: authorised and never captured, voided or expired.
    start = _at(date(2026, 10, 2), 15)
    payment(17, usd, [("initiated", start, 2000), ("authorised", start + timedelta(seconds=5), 2000)])


# ------------------------------------------------------------------------------------------- world and delivery


def build_world(seed: int = SEED) -> World:
    rng = random.Random(seed)
    merchants = _merchants(rng)
    events, settled = _population(rng, merchants)
    lines = _settlement_lines(settled)
    world = World(merchants, [], events, lines)
    _plant(world)

    # Ids after a stable sort, so the same seed always gives the same ids. Planted records keep theirs.
    world.events = [e for e in world.events if e.ingestion_time.date() <= LAST_REPORTED_DAY]
    world.events.sort(key=lambda e: (e.ingestion_time, e.payment_id, e.sequence_no))
    for number, event in enumerate((e for e in world.events if not e.event_id), start=1):
        event.event_id = f"evt-{number:09d}"
    world.lines.sort(
        key=lambda line: (line.currency, line.settlement_date, line.payment_reference, line.line_type)
    )
    counters: dict[tuple, int] = {}
    for line in world.lines:
        if not line.settlement_line_id:
            key = (line.currency, line.settlement_date)
            counters[key] = counters.get(key, 0) + 1
            line.settlement_line_id = f"stl-{line.currency}-{line.settlement_date:%Y%m%d}-{counters[key]:06d}"
    return world


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _merchant_row(m: Merchant) -> dict:
    return {
        "merchant_id": m.merchant_id,
        "merchant_name": f"Merchant {m.merchant_id[1:]}",
        "country": COUNTRIES[m.currency],
        "currency": m.currency,
        "fee_bps": m.fee_bps,
        "fee_fixed_minor": m.fee_fixed_minor,
        "updated_at": _ts(m.updated_at),
    }


def deliver(world: World, out: Path) -> list[Path]:
    """Write one folder per arrival day, each with a manifest of its files' rows and checksums."""
    files: dict[date, dict[str, tuple[str, list[dict]]]] = {}

    def put(arrival: date, name: str, kind: str, rows: list[dict]) -> None:
        files.setdefault(arrival, {})[name] = (kind, rows)

    put(
        FIRST_PAYMENT_DAY + timedelta(days=1),
        "merchants.csv",
        "merchants",
        [_merchant_row(m) for m in world.merchants],
    )
    for m in world.late_merchants:
        day = m.updated_at.date()
        put(day + timedelta(days=1), f"merchants_{day}.csv", "merchants", [_merchant_row(m)])

    day = FIRST_PAYMENT_DAY
    while day <= LAST_REPORTED_DAY:
        rows = []
        for e in (e for e in world.events if e.ingestion_time.date() == day):
            row = {
                "event_id": e.event_id,
                "payment_id": e.payment_id,
                "merchant_id": e.merchant_id,
                "event_type": e.event_type,
                "sequence_no": e.sequence_no,
                "event_time": _ts(e.event_time),
                "ingestion_time": _ts(e.ingestion_time),
                "amount_minor": e.amount_minor,
                "currency": e.currency,
            }
            rows.extend([row, dict(row)] if e.event_id in world.duplicated_event_ids else [row])
        put(day + timedelta(days=1), f"events_{day}.csv", "events", rows)
        for currency in CURRENCIES:
            name = f"settlement_{currency}_{day}.csv"
            rows = [
                {
                    "settlement_line_id": line.settlement_line_id,
                    "settlement_date": line.settlement_date.isoformat(),
                    "payment_reference": line.payment_reference,
                    "line_type": line.line_type,
                    "currency": line.currency,
                    "gross_minor": line.gross_minor,
                    "fee_minor": line.fee_minor,
                    "net_minor": line.net_minor,
                }
                for line in world.lines
                if line.currency == currency and line.settlement_date == day
            ]
            arrival = world.late_files.get(name, day + timedelta(days=1))
            put(arrival, name, "settlement", rows)
            if name in world.resent_files:
                put(world.resent_files[name], name, "settlement", rows)
        day += timedelta(days=1)

    manifests = []
    for arrival in sorted(files):
        folder = out / f"arrival_{arrival}"
        folder.mkdir(parents=True, exist_ok=True)
        entries = []
        for name in sorted(files[arrival]):
            kind, rows = files[arrival][name]
            path = folder / name
            _write_csv(path, FILE_COLUMNS[kind], rows)
            entries.append(
                {
                    "file": name,
                    "kind": kind,
                    "rows": len(rows),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        manifest = folder / "manifest.json"
        manifest.write_text(
            json.dumps({"arrival_date": arrival.isoformat(), "files": entries}, indent=2) + "\n",
            encoding="utf-8",
        )
        manifests.append(manifest)
    return manifests


def generate(out: Path, seed: int = SEED) -> list[Path]:
    return deliver(build_world(seed), out)
