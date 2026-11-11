"""Paths, dates and the source-file contract shared by the generator and the loader."""

import os
from datetime import date
from pathlib import Path

CASE_ROOT = Path(__file__).resolve().parents[2]

SEED = 2602
PERIOD_START = date(2024, 10, 1)
PERIOD_END = date(2026, 9, 30)
BATCH_1_END = date(2026, 9, 16)

# Each batch is one extract covering a date window; batch 2 also carries late material for batch 1's window.
BATCH_WINDOWS: dict[int, tuple[date, date]] = {
    1: (PERIOD_START, BATCH_1_END),
    2: (date(2026, 9, 17), PERIOD_END),
}

# The contract for each extract: file name stem and column order. The loader rejects a file whose header differs,
# so a schema change in a source fails the load instead of shifting columns silently.
SOURCE_COLUMNS: dict[str, list[str]] = {
    "reference_stores": [
        "store_id",
        "store_name",
        "region",
        "store_format",
        "opened_date",
        "effective_from_date",
        "updated_at",
    ],
    "reference_products": ["product_id", "product_name", "category", "list_price", "updated_at"],
    "loyalty_members": [
        "member_id",
        "enrolled_at",
        "home_store_id",
        "enrolment_channel",
        "birth_month",
        "updated_at",
    ],
    "pos_transactions": [
        "transaction_id",
        "store_id",
        "member_id",
        "transaction_at",
        "transaction_type",
        "original_transaction_id",
        "updated_at",
    ],
    "pos_transaction_lines": [
        "transaction_id",
        "line_number",
        "product_id",
        "quantity",
        "unit_price",
        "discount_amount",
        "currency_code",
        "original_line_number",
    ],
    "loyalty_events": [
        "event_id",
        "member_id",
        "event_type",
        "event_at",
        "transaction_id",
        "entitlement_id",
        "points",
    ],
    "loyalty_reward_entitlements": [
        "entitlement_id",
        "member_id",
        "reward_type",
        "issued_at",
        "expires_at",
        "status",
        "redeemed_at",
        "redeemed_transaction_id",
        "updated_at",
    ],
}


def data_dir() -> Path:
    return Path(os.environ.get("LOYALTY_DATA_DIR", CASE_ROOT / "data" / "generated"))


def warehouse_path() -> Path:
    return Path(os.environ.get("LOYALTY_WAREHOUSE", CASE_ROOT / "warehouse" / "loyalty.duckdb"))
