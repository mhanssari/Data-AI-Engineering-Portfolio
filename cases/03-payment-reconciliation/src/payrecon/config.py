"""Dates, the delivery calendar, and the column contract of every source file."""

import os
from datetime import date, timedelta
from pathlib import Path

CASE_ROOT = Path(__file__).resolve().parents[2]

SEED = 3303
CURRENCIES = ("GBP", "EUR", "USD")
MERCHANT_COUNT = 40

# Payments are initiated on 1-30 October 2026. Events and settlements are reported for 1-31 October, and arrive the
# next morning in an arrival folder: arrival A holds the event file and settlement files for day A - 1.
FIRST_PAYMENT_DAY = date(2026, 10, 1)
LAST_PAYMENT_DAY = date(2026, 10, 30)
LAST_REPORTED_DAY = date(2026, 10, 31)
FIRST_ARRIVAL = FIRST_PAYMENT_DAY + timedelta(days=1)
LAST_ARRIVAL = LAST_REPORTED_DAY + timedelta(days=1)

# Settlement rules (docs/requirements.md). A payment captured on day D is due in the file for D, and pending until
# the file for D + GRACE_DAYS has been processed.
GRACE_DAYS = 1

FILE_COLUMNS: dict[str, list[str]] = {
    "merchants": [
        "merchant_id",
        "merchant_name",
        "country",
        "currency",
        "fee_bps",
        "fee_fixed_minor",
        "updated_at",
    ],
    "events": [
        "event_id",
        "payment_id",
        "merchant_id",
        "event_type",
        "sequence_no",
        "event_time",
        "ingestion_time",
        "amount_minor",
        "currency",
    ],
    "settlement": [
        "settlement_line_id",
        "settlement_date",
        "payment_reference",
        "line_type",
        "currency",
        "gross_minor",
        "fee_minor",
        "net_minor",
    ],
}

EVENT_TYPES = ("initiated", "authorised", "declined", "captured", "voided", "expired", "refunded", "reversed")
LINE_TYPES = ("sale", "refund", "reversal")


def file_kind(name: str) -> str:
    """merchants*.csv, events_<day>.csv, settlement_<CCY>_<day>.csv."""
    for kind in ("merchants", "events", "settlement"):
        if name.startswith(kind):
            return kind
    raise ValueError(f"unknown file {name}")


def data_dir() -> Path:
    return Path(os.environ.get("PAYRECON_DATA_DIR", CASE_ROOT / "data" / "generated"))


def arrival_days() -> list[date]:
    return [FIRST_ARRIVAL + timedelta(days=n) for n in range((LAST_ARRIVAL - FIRST_ARRIVAL).days + 1)]
