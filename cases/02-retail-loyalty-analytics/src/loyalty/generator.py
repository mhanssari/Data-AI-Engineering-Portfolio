"""Seeded generator for the retailer's source extracts.

The whole observation period is simulated once, then cut into two batch extracts by the time each record was known
to its source system. Same seed, same bytes: every random choice comes from one `random.Random` consumed in a fixed
order, and identifiers are assigned after a stable sort.

The planted defects in `scenarios.py` are added as explicit records on top of the random population.
"""

import csv
import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from loyalty.config import BATCH_WINDOWS, PERIOD_END, PERIOD_START, SEED, SOURCE_COLUMNS

CENT = Decimal("0.01")
MEMBER_COUNT = 6000
LATE_PRODUCT_ID = "P061"
LATE_PRODUCT_ON_SALE = date(2026, 8, 1)
POINTS_PER_VOUCHER = 200

# store_id, name, region, format, opened
STORES = [
    ("S001", "Store 001", "London", "high_street", date(2012, 4, 2)),
    ("S002", "Store 002", "South East", "retail_park", date(2014, 6, 1)),
    ("S003", "Store 003", "Midlands", "high_street", date(2015, 3, 9)),
    ("S004", "Store 004", "North", "retail_park", date(2016, 9, 5)),
    ("S005", "Store 005", "Scotland", "high_street", date(2017, 5, 22)),
    ("S006", "Store 006", "South West", "outlet", date(2018, 7, 2)),
    ("S007", "Store 007", "Midlands", "retail_park", date(2018, 11, 12)),
    ("S008", "Store 008", "London", "outlet", date(2019, 3, 4)),
    ("S009", "Store 009", "North", "high_street", date(2020, 10, 5)),
    ("S010", "Store 010", "South East", "high_street", date(2021, 2, 15)),
    ("S011", "Store 011", "Wales", "retail_park", date(2022, 8, 1)),
    ("S012", "Store 012", "North", "retail_park", date(2025, 6, 16)),
]
# Attribute changes (D9): a refit, a reorganisation, and one that takes effect in batch 2's window.
STORE_CHANGES = [
    ("S003", date(2025, 9, 1), "store_format", "retail_park"),
    ("S007", date(2026, 1, 1), "region", "North"),
    ("S005", date(2026, 9, 20), "store_format", "retail_park"),
]
CATEGORIES = ["Personal care", "Household", "Food cupboard", "Beverages", "Baby"]

REWARD_VALIDITY_DAYS = {"welcome_offer": 30, "birthday_offer": 30, "points_voucher": 90}
REDEEM_PROBABILITY = {"welcome_offer": 0.5, "birthday_offer": 0.4, "points_voucher": 0.75}


@dataclass
class Line:
    line_number: int
    product_id: str
    quantity: int
    unit_price: Decimal
    discount_amount: Decimal
    original_line_number: int | None = None

    @property
    def net(self) -> Decimal:
        return self.quantity * self.unit_price - self.discount_amount


@dataclass
class Transaction:
    at: datetime
    store_id: str
    member_id: str | None
    transaction_type: str
    lines: list[Line]
    original: "Transaction | None" = None
    original_id: str | None = None  # for a return whose original is not in the data (D5)
    id: str = ""

    @property
    def net(self) -> Decimal:
        return sum((line.net for line in self.lines), Decimal("0"))


@dataclass
class Entitlement:
    member_id: str
    reward_type: str
    issued_at: datetime
    expires_at: datetime
    redeemed_at: datetime | None = None
    redeemed_transaction: Transaction | None = None
    cancelled_at: datetime | None = None
    status_stuck_at_issued: bool = False  # D13, D14: the status job never updated this row
    decided: bool = False
    id: str = ""


@dataclass
class Event:
    at: datetime
    member_id: str
    event_type: str
    transaction: Transaction | None = None
    entitlement: Entitlement | None = None
    points: int | None = None
    id: str = ""


@dataclass
class Member:
    member_id: str
    enrolled_at: datetime
    home_store_id: str
    enrolment_channel: str
    birth_month: int
    updated_at: datetime
    correction: tuple[datetime, datetime] | None = None  # (corrected enrolled_at, when corrected), D12


@dataclass
class World:
    store_versions: list[dict[str, str]]
    products: list[dict[str, str]]
    members: list[Member]
    transactions: list[Transaction]
    entitlements: list[Entitlement]
    events: list[Event]
    # Explicit re-deliveries: (batch, kind, record, overrides)
    redeliveries: list[tuple[int, str, object, dict[str, str]]] = field(default_factory=list)
    in_file_duplicates: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------------------------- formatting


def _ts(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT)}"


def _at(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute, second))


# ---------------------------------------------------------------------------------------------- reference data


def _store_versions() -> list[dict[str, str]]:
    versions = []
    for store_id, name, region, store_format, opened in STORES:
        current = {"region": region, "store_format": store_format}
        versions.append((store_id, name, opened, opened, dict(current), _at(opened, 8)))
        for changed_id, effective, attribute, value in STORE_CHANGES:
            if changed_id == store_id:
                current[attribute] = value
                # Recorded in the store system the evening before it takes effect.
                versions.append(
                    (store_id, name, opened, effective, dict(current), _at(effective, 18) - timedelta(days=1))
                )
    return [
        {
            "store_id": store_id,
            "store_name": name,
            "region": attrs["region"],
            "store_format": attrs["store_format"],
            "opened_date": opened.isoformat(),
            "effective_from_date": effective.isoformat(),
            "updated_at": _ts(recorded),
        }
        for store_id, name, opened, effective, attrs, recorded in versions
    ]


def _products(rng: random.Random) -> list[dict[str, str]]:
    products = []
    for number in range(1, 61):
        category = CATEGORIES[(number - 1) % len(CATEGORIES)]
        price = Decimal(f"{rng.randint(1, 24)}.{rng.choice(['49', '99'])}")
        products.append(
            {
                "product_id": f"P{number:03d}",
                "product_name": f"{category} item {number:02d}",
                "category": category,
                "list_price": _money(price),
                "updated_at": "2024-09-01 09:00:00",
            }
        )
    # D10: on sale from 1 August 2026, but master data only records it in batch 2's window.
    products.append(
        {
            "product_id": LATE_PRODUCT_ID,
            "product_name": "Seasonal gift set",
            "category": "Gifts",
            "list_price": "18.00",
            "updated_at": "2026-09-20 10:00:00",
        }
    )
    return products


def _open_stores(day: date) -> list[str]:
    return [store_id for store_id, _, _, _, opened in STORES if opened <= day]


# ---------------------------------------------------------------------------------------------- random population


def _poisson(rng: random.Random, mean: float) -> int:
    limit, count, product = math.exp(-mean), 0, rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


class _Basket:
    def __init__(self, rng: random.Random, products: list[dict[str, str]]):
        self.rng = rng
        regular = [p for p in products if p["product_id"] != LATE_PRODUCT_ID]
        self.regular = [(p["product_id"], Decimal(p["list_price"])) for p in regular]
        self.regular_weights = [rng.random() + 0.2 for _ in regular]
        self.late = (LATE_PRODUCT_ID, Decimal("18.00"))

    def lines(self, day: date) -> list[Line]:
        rng = self.rng
        count = rng.choices([1, 2, 3, 4, 5], [35, 30, 18, 10, 7])[0]
        chosen: list[tuple[str, Decimal]] = []
        while len(chosen) < count:
            if day >= LATE_PRODUCT_ON_SALE and rng.random() < 0.03:
                candidate = self.late
            else:
                candidate = rng.choices(self.regular, self.regular_weights)[0]
            if candidate not in chosen:
                chosen.append(candidate)
        lines = []
        for number, (product_id, price) in enumerate(chosen, start=1):
            quantity = rng.choices([1, 2, 3], [70, 22, 8])[0]
            # Discounts are per unit, so a partial return refunds an exact share of the discount.
            unit_discount = (
                (price * Decimal("0.10")).quantize(CENT, ROUND_HALF_UP)
                if rng.random() < 0.2
                else Decimal("0")
            )
            lines.append(Line(number, product_id, quantity, price, unit_discount * quantity))
        return lines


def _random_time(rng: random.Random, day: date) -> datetime:
    return _at(day, rng.randint(8, 19), rng.randint(0, 59), rng.randint(0, 59))


def _members_and_sales(rng: random.Random, basket: _Basket) -> tuple[list[Member], list[Transaction]]:
    period_days = (PERIOD_END - PERIOD_START).days + 1
    members, sales = [], []
    for number in range(1, MEMBER_COUNT + 1):
        enrol_day = PERIOD_START + timedelta(days=int(rng.random() * period_days))
        enrolled_at = _at(enrol_day, rng.randint(9, 19), rng.randint(0, 59))
        home_store = rng.choice(_open_stores(enrol_day))
        channel = rng.choices(["store", "web", "app"], [60, 25, 15])[0]
        member = Member(f"M{number:05d}", enrolled_at, home_store, channel, rng.randint(1, 12), enrolled_at)
        members.append(member)

        never_buys = rng.random() < 0.12
        rate_per_day = rng.choices([0.3, 0.6, 1.0, 2.0], [35, 35, 20, 10])[0] / 30
        last_day = min(PERIOD_END, enrol_day + timedelta(days=int(rng.expovariate(1 / 300))))
        same_day = channel == "store" and rng.random() < 0.5
        day = enrol_day if same_day else enrol_day + timedelta(days=1 + int(rng.expovariate(1 / 20)))
        if never_buys:
            continue
        while day <= last_day:
            if day == enrol_day:
                at = enrolled_at + timedelta(minutes=rng.randint(5, 120))
            else:
                at = _random_time(rng, day)
            store = home_store if rng.random() < 0.8 else rng.choice(_open_stores(day))
            sales.append(Transaction(at, store, member.member_id, "sale", basket.lines(day)))
            day += timedelta(days=max(1, math.ceil(rng.expovariate(rate_per_day))))
    return members, sales


def _anonymous_sales(rng: random.Random, basket: _Basket) -> list[Transaction]:
    traffic = {store_id: 1.6 + rng.random() * 2.0 for store_id, *_ in STORES}
    sales = []
    day = PERIOD_START
    while day <= PERIOD_END:
        weekend = 1.3 if day.weekday() >= 5 else 1.0
        for store_id in _open_stores(day):
            for _ in range(_poisson(rng, traffic[store_id] * weekend)):
                sales.append(Transaction(_random_time(rng, day), store_id, None, "sale", basket.lines(day)))
        day += timedelta(days=1)
    return sales


def _returns(rng: random.Random, sales: list[Transaction]) -> list[Transaction]:
    returns = []
    for sale in sales:
        by_day: dict[date, list[Line]] = {}
        for line in sale.lines:
            if rng.random() >= 0.03:
                continue
            day = sale.at.date() + timedelta(days=rng.randint(1, 45))
            quantity = rng.randint(1, line.quantity)
            unit_discount = line.discount_amount / line.quantity
            by_day.setdefault(day, []).append(
                Line(
                    0,
                    line.product_id,
                    -quantity,
                    line.unit_price,
                    -unit_discount * quantity,
                    line.line_number,
                )
            )
        for day in sorted(by_day):
            if day > PERIOD_END:
                continue
            lines = by_day[day]
            for number, line in enumerate(lines, start=1):
                line.line_number = number
            store = sale.store_id if rng.random() < 0.9 else rng.choice(_open_stores(day))
            returns.append(
                Transaction(_random_time(rng, day), store, sale.member_id, "return", lines, original=sale)
            )
    return returns


def _loyalty(
    rng: random.Random, members: list[Member], transactions: list[Transaction]
) -> tuple[list[Entitlement], list[Event]]:
    by_member: dict[str, list[Transaction]] = {}
    for txn in transactions:
        if txn.member_id:
            by_member.setdefault(txn.member_id, []).append(txn)

    entitlements: list[Entitlement] = []
    events: list[Event] = []

    def issue(member_id: str, reward_type: str, at: datetime) -> Entitlement:
        expires = at + timedelta(days=REWARD_VALIDITY_DAYS[reward_type])
        ent = Entitlement(member_id, reward_type, at, expires)
        if rng.random() < 0.02:
            ent.cancelled_at = at + timedelta(days=rng.randint(1, REWARD_VALIDITY_DAYS[reward_type] - 5))
        entitlements.append(ent)
        events.append(Event(at, member_id, "reward_issued", entitlement=ent))
        return ent

    for member in members:
        events.append(Event(member.enrolled_at, member.member_id, "enrolment"))
        open_rewards = [issue(member.member_id, "welcome_offer", member.enrolled_at + timedelta(seconds=1))]
        for year in (2024, 2025, 2026):
            birthday = _at(date(year, member.birth_month, 1), 6)
            if member.enrolled_at < birthday <= _at(PERIOD_END, 23, 59):
                open_rewards.append(issue(member.member_id, "birthday_offer", birthday))

        points = vouchers = 0
        for txn in sorted(by_member.get(member.member_id, []), key=lambda t: t.at):
            if txn.transaction_type == "return":
                reversed_points = math.floor(-txn.net)
                if reversed_points:
                    events.append(
                        Event(txn.at, member.member_id, "points_reversed", txn, points=-reversed_points)
                    )
                    points -= reversed_points
                continue
            # One redemption opportunity per reward: the first purchase while it is valid.
            for ent in open_rewards:
                cancelled_before = ent.cancelled_at is not None and ent.cancelled_at <= txn.at
                if ent.decided or cancelled_before or not ent.issued_at < txn.at < ent.expires_at:
                    continue
                ent.decided = True
                if rng.random() < REDEEM_PROBABILITY[ent.reward_type]:
                    ent.redeemed_at = txn.at + timedelta(seconds=3)
                    ent.redeemed_transaction = txn
                    ent.cancelled_at = None
                    events.append(Event(ent.redeemed_at, member.member_id, "reward_redeemed", txn, ent))
            earned = math.floor(txn.net)
            events.append(Event(txn.at, member.member_id, "points_earned", txn, points=earned))
            points += earned
            if rng.random() < 0.08:
                events.append(Event(txn.at, member.member_id, "bonus_points", txn, points=50))
                points += 50
            while points >= (vouchers + 1) * POINTS_PER_VOUCHER:
                vouchers += 1
                at = txn.at + timedelta(seconds=2)
                if at.date() <= PERIOD_END:
                    open_rewards.append(issue(member.member_id, "points_voucher", at))
    return entitlements, events


# ---------------------------------------------------------------------------------------------- planted defects


def _plant(world: World) -> None:
    txns, ents, events, members = world.transactions, world.entitlements, world.events, world.members

    def sale(txn_id, at, store, member, lines, kind="sale", original=None, original_id=None):
        txn = Transaction(at, store, member, kind, lines, original, original_id, id=txn_id)
        txns.append(txn)
        return txn

    def line(number, product, quantity, price, original_line=None):
        return Line(number, product, quantity, Decimal(price), Decimal("0"), original_line)

    def member(member_id, enrolled_at, store, channel, birth_month):
        m = Member(member_id, enrolled_at, store, channel, birth_month, enrolled_at)
        members.append(m)
        return m

    def event(event_id, at, member_id, kind, txn=None, ent=None, points=None):
        events.append(Event(at, member_id, kind, txn, ent, points, id=event_id))

    def reward(ent_id, member_id, kind, issued_at, redeemed_txn=None, redeemed_at=None, stuck=False):
        expires = issued_at + timedelta(days=REWARD_VALIDITY_DAYS[kind])
        ent = Entitlement(
            member_id, kind, issued_at, expires, redeemed_at, redeemed_txn, None, stuck, True, ent_id
        )
        ents.append(ent)
        return ent

    # D1: duplicate delivery inside batch 1, and again across batches.
    sale("T9000001", _at(date(2026, 5, 4), 12), "S001", None, [line(1, "P001", 1, "10.00")])
    world.in_file_duplicates.add("T9000001")
    t2 = sale("T9000002", _at(date(2026, 9, 15), 15, 30), "S002", None, [line(1, "P002", 2, "5.00")])
    world.redeliveries.append((2, "transaction", t2, {}))

    # D3 and D2: one purchase, three events; the member's enrolment event is duplicated inside batch 1.
    m1 = member("M90001", _at(date(2026, 3, 2), 10), "S001", "store", 7)
    event("E900000001", m1.enrolled_at, "M90001", "enrolment")
    world.in_file_duplicates.add("E900000001")
    welcome = reward("R9000001", "M90001", "welcome_offer", m1.enrolled_at + timedelta(seconds=1))
    t3 = sale("T9000003", _at(date(2026, 3, 2), 10, 30), "S001", "M90001", [line(1, "P003", 2, "20.00")])
    welcome.redeemed_transaction, welcome.redeemed_at = t3, t3.at + timedelta(seconds=3)
    event("E900000006", welcome.issued_at, "M90001", "reward_issued", ent=welcome)
    event("E900000003", t3.at, "M90001", "points_earned", t3, points=40)
    event("E900000004", t3.at, "M90001", "bonus_points", t3, points=50)
    event("E900000005", welcome.redeemed_at, "M90001", "reward_redeemed", t3, welcome)

    # D4: partial return in batch 1, the rest of the sale returned in batch 2.
    t4 = sale(
        "T9000004",
        _at(date(2026, 8, 20), 14),
        "S004",
        None,
        [line(1, "P004", 2, "15.00"), line(2, "P005", 1, "30.00")],
    )
    sale("T9000005", _at(date(2026, 9, 1), 11), "S004", None, [line(1, "P004", -1, "15.00", 1)], "return", t4)
    sale(
        "T9000006", _at(date(2026, 9, 22), 16), "S004", None, [line(1, "P005", -1, "30.00", 2)], "return", t4
    )

    # D5: return of a sale that is not in the extract.
    sale(
        "T9000007",
        _at(date(2026, 2, 14), 13),
        "S006",
        None,
        [line(1, "P006", -1, "12.00", 1)],
        "return",
        None,
        "T8999999",
    )

    # D6: more units returned than sold.
    t8 = sale("T9000008", _at(date(2026, 4, 10), 10), "S007", None, [line(1, "P007", 2, "8.00")])
    sale("T9000009", _at(date(2026, 4, 12), 10), "S007", None, [line(1, "P007", -3, "8.00", 1)], "return", t8)

    # D7: anonymous sale.
    sale("T9000010", _at(date(2026, 6, 6), 12), "S008", None, [line(1, "P008", 1, "9.99")])

    # D8 and D2: a member who never buys; the enrolment event is delivered again in batch 2.
    m2 = member("M90002", _at(date(2025, 1, 15), 9, 30), "S009", "web", 3)
    event("E900000002", m2.enrolled_at, "M90002", "enrolment")
    world.redeliveries.append((2, "event", events[-1], {}))

    # D9: either side of S003's format change.
    sale("T9000011", _at(date(2025, 8, 31), 12), "S003", None, [line(1, "P009", 1, "5.00")])
    sale("T9000012", _at(date(2025, 9, 1), 12), "S003", None, [line(1, "P009", 1, "5.00")])

    # D10: the late product, sold in batch 1.
    sale("T9000013", _at(date(2026, 8, 15), 12), "S010", None, [line(1, LATE_PRODUCT_ID, 1, "18.00")])

    # D11: anonymous in batch 1; the member claims the purchase in batch 2.
    m3 = member("M90003", _at(date(2026, 6, 1), 10), "S011", "app", 1)
    event("E900000007", m3.enrolled_at, "M90003", "enrolment")
    t14 = sale("T9000014", _at(date(2026, 9, 10), 17), "S011", None, [line(1, "P010", 1, "25.00")])
    claimed_at = _at(date(2026, 9, 18), 10)
    world.redeliveries.append(
        (2, "transaction_header", t14, {"member_id": "M90003", "updated_at": _ts(claimed_at)})
    )
    event("E900000008", claimed_at, "M90003", "points_earned", t14, points=25)

    # D12: enrolment date corrected in batch 2, moving the member from the March to the February cohort.
    m4 = member("M90004", _at(date(2025, 3, 10), 11), "S001", "store", 5)
    m4.correction = (_at(date(2025, 2, 24), 11), _at(date(2026, 9, 18), 9))
    event("E900000009", m4.enrolled_at, "M90004", "enrolment")
    t16 = sale("T9000016", _at(date(2025, 3, 15), 12), "S001", "M90004", [line(1, "P011", 1, "10.00")])
    t17 = sale("T9000017", _at(date(2025, 4, 1), 12), "S001", "M90004", [line(1, "P011", 1, "10.00")])
    event("E900000010", t16.at, "M90004", "points_earned", t16, points=10)
    event("E900000011", t17.at, "M90004", "points_earned", t17, points=10)

    # D13 and D14: status column never updated, once after a redemption and once after expiry.
    m5 = member("M90005", _at(date(2025, 10, 1), 10), "S002", "store", 11)
    event("E900000016", m5.enrolled_at, "M90005", "enrolment")
    t18 = sale("T9000018", _at(date(2025, 10, 5), 12), "S002", "M90005", [line(1, "P012", 1, "20.00")])
    redeemed = reward(
        "R9000002", "M90005", "welcome_offer", m5.enrolled_at + timedelta(seconds=1), stuck=True
    )
    redeemed.redeemed_transaction, redeemed.redeemed_at = t18, t18.at + timedelta(seconds=3)
    event("E900000013", redeemed.issued_at, "M90005", "reward_issued", ent=redeemed)
    event("E900000012", t18.at, "M90005", "points_earned", t18, points=20)
    event("E900000014", redeemed.redeemed_at, "M90005", "reward_redeemed", t18, redeemed)
    expired = reward("R9000003", "M90005", "birthday_offer", _at(date(2025, 11, 1), 6), stuck=True)
    event("E900000015", expired.issued_at, "M90005", "reward_issued", ent=expired)

    # D15: enrolled and first purchase days before the as-of date.
    m6 = member("M90006", _at(date(2026, 9, 25), 10), "S012", "store", 2)
    event("E900000017", m6.enrolled_at, "M90006", "enrolment")
    t15 = sale("T9000015", _at(date(2026, 9, 26), 11), "S012", "M90006", [line(1, "P013", 1, "7.50")])
    event("E900000018", t15.at, "M90006", "points_earned", t15, points=7)


# ---------------------------------------------------------------------------------------------- world


def build_world(seed: int = SEED) -> World:
    rng = random.Random(seed)
    store_versions = _store_versions()
    products = _products(rng)
    basket = _Basket(rng, products)
    members, member_sales = _members_and_sales(rng, basket)
    sales = member_sales + _anonymous_sales(rng, basket)
    sales.sort(key=lambda t: t.at)
    transactions = sales + _returns(rng, sales)
    transactions.sort(key=lambda t: t.at)
    for number, txn in enumerate(transactions, start=1):
        txn.id = f"T{number:07d}"
    entitlements, events = _loyalty(rng, members, transactions)

    event_order = {
        "enrolment": 0,
        "reward_issued": 1,
        "points_earned": 2,
        "bonus_points": 3,
        "reward_redeemed": 4,
        "points_reversed": 5,
    }
    events.sort(key=lambda e: (e.at, e.member_id, event_order[e.event_type]))
    for number, event in enumerate(events, start=1):
        event.id = f"E{number:09d}"
    entitlements.sort(key=lambda e: (e.issued_at, e.member_id, e.reward_type))
    for number, ent in enumerate(entitlements, start=1):
        ent.id = f"R{number:07d}"

    world = World(store_versions, products, members, transactions, entitlements, events)
    _plant(world)
    return world


# ---------------------------------------------------------------------------------------------- extracts


def _entitlement_state(ent: Entitlement, as_of: datetime) -> tuple[str, datetime, datetime | None]:
    """Status, last change and redemption time as the programme's status column would show them at `as_of`."""
    if ent.status_stuck_at_issued:
        return "issued", ent.issued_at, None
    if ent.redeemed_at and ent.redeemed_at <= as_of:
        return "redeemed", ent.redeemed_at, ent.redeemed_at
    if ent.cancelled_at and ent.cancelled_at <= as_of:
        return "cancelled", ent.cancelled_at, None
    if ent.expires_at <= as_of:
        return "expired", ent.expires_at, None
    return "issued", ent.issued_at, None


def _transaction_row(txn: Transaction) -> dict[str, str]:
    return {
        "transaction_id": txn.id,
        "store_id": txn.store_id,
        "member_id": txn.member_id or "",
        "transaction_at": _ts(txn.at),
        "transaction_type": txn.transaction_type,
        "original_transaction_id": txn.original.id if txn.original else (txn.original_id or ""),
        "updated_at": _ts(txn.at),
    }


def _line_rows(txn: Transaction) -> list[dict[str, str]]:
    return [
        {
            "transaction_id": txn.id,
            "line_number": str(line.line_number),
            "product_id": line.product_id,
            "quantity": str(line.quantity),
            "unit_price": _money(line.unit_price),
            "discount_amount": _money(line.discount_amount),
            "currency_code": "GBP",
            "original_line_number": ""
            if line.original_line_number is None
            else str(line.original_line_number),
        }
        for line in txn.lines
    ]


def _event_row(event: Event) -> dict[str, str]:
    return {
        "event_id": event.id,
        "member_id": event.member_id,
        "event_type": event.event_type,
        "event_at": _ts(event.at),
        "transaction_id": event.transaction.id if event.transaction else "",
        "entitlement_id": event.entitlement.id if event.entitlement else "",
        "points": "" if event.points is None else str(event.points),
    }


def _member_row(member: Member, as_of: datetime) -> dict[str, str]:
    enrolled_at, updated_at = member.enrolled_at, member.updated_at
    if member.correction and member.correction[1] <= as_of:
        enrolled_at, updated_at = member.correction
    return {
        "member_id": member.member_id,
        "enrolled_at": _ts(enrolled_at),
        "home_store_id": member.home_store_id,
        "enrolment_channel": member.enrolment_channel,
        "birth_month": str(member.birth_month),
        "updated_at": _ts(updated_at),
    }


def extract(world: World, batch: int) -> dict[str, list[dict[str, str]]]:
    start_day, end_day = BATCH_WINDOWS[batch]
    start, end = _at(start_day, 0), _at(end_day, 23, 59, 59)

    def in_window(at: datetime) -> bool:
        return start <= at <= end

    # Reference data is a full extract each time.
    stores = [v for v in world.store_versions if v["updated_at"] <= _ts(end)]
    products = [p for p in world.products if p["updated_at"] <= _ts(end)]

    # Members: batch 1 is the state at its end; later batches send new and corrected members only.
    members = []
    for member in world.members:
        corrected_now = member.correction is not None and in_window(member.correction[1])
        if (batch == 1 and member.enrolled_at <= end) or (
            batch > 1 and (in_window(member.enrolled_at) or corrected_now)
        ):
            members.append(_member_row(member, end))

    transactions, lines = [], []
    for txn in sorted(world.transactions, key=lambda t: t.id):
        if in_window(txn.at):
            copies = 2 if txn.id in world.in_file_duplicates else 1
            for _ in range(copies):
                transactions.append(_transaction_row(txn))
                lines.extend(_line_rows(txn))

    events = []
    for event in sorted(world.events, key=lambda e: e.id):
        if in_window(event.at):
            copies = 2 if event.id in world.in_file_duplicates else 1
            events.extend(_event_row(event) for _ in range(copies))

    entitlements = []
    for ent in sorted(world.entitlements, key=lambda e: e.id):
        if ent.issued_at > end:
            continue
        status, changed_at, redeemed_at = _entitlement_state(ent, end)
        if batch > 1 and not (in_window(ent.issued_at) or in_window(changed_at)):
            continue
        entitlements.append(
            {
                "entitlement_id": ent.id,
                "member_id": ent.member_id,
                "reward_type": ent.reward_type,
                "issued_at": _ts(ent.issued_at),
                "expires_at": _ts(ent.expires_at),
                "status": status,
                "redeemed_at": _ts(redeemed_at),
                "redeemed_transaction_id": ent.redeemed_transaction.id
                if redeemed_at and ent.redeemed_transaction
                else "",
                "updated_at": _ts(changed_at),
            }
        )

    for redelivery_batch, kind, record, overrides in world.redeliveries:
        if redelivery_batch != batch:
            continue
        if kind == "transaction":
            transactions.append(_transaction_row(record))
            lines.extend(_line_rows(record))
        elif kind == "transaction_header":
            transactions.append({**_transaction_row(record), **overrides})
        elif kind == "event":
            events.append(_event_row(record))

    return {
        "reference_stores": stores,
        "reference_products": products,
        "loyalty_members": members,
        "pos_transactions": transactions,
        "pos_transaction_lines": lines,
        "loyalty_events": events,
        "loyalty_reward_entitlements": entitlements,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_batch(out_dir: Path, batch: int, tables: dict[str, list[dict[str, str]]], seed: int) -> Path:
    batch_dir = out_dir / f"batch_{batch}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for table, columns in SOURCE_COLUMNS.items():
        path = batch_dir / f"{table}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(tables[table])
        files.append({"table": table, "file": path.name, "rows": len(tables[table]), "sha256": _sha256(path)})
    start, end = BATCH_WINDOWS[batch]
    manifest = {
        "batch_id": batch,
        "seed": seed,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "files": files,
    }
    manifest_path = batch_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def generate(out_dir: Path, seed: int = SEED) -> list[Path]:
    world = build_world(seed)
    return [write_batch(out_dir, batch, extract(world, batch), seed) for batch in sorted(BATCH_WINDOWS)]
