"""Landing of one arrival folder into raw, with row-level validation and a quarantine route.

- The manifest is checked first: every file present, checksum and column header as agreed. A failure rejects the
  whole arrival before anything is written.
- Each row is then validated. A malformed row goes to ops.quarantine with its file, row number, raw content and
  reason; the rest of the file still lands (acceptance A4).
- The whole arrival lands in one transaction. A file already loaded with the same name and checksum is skipped, so
  a re-sent file lands nothing (planted P12).
"""

import csv
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from payrecon.config import CURRENCIES, EVENT_TYPES, FILE_COLUMNS, LINE_TYPES


class LoadError(Exception):
    """The arrival was rejected; nothing was written."""


@dataclass(frozen=True)
class FileResult:
    file_name: str
    loaded: int
    quarantined: int
    skipped: bool


class RowError(ValueError):
    pass


def _int(row: dict, key: str, *, minimum: int | None = None) -> int:
    try:
        value = int(row[key])
    except (TypeError, ValueError):
        raise RowError(f"{key} is not an integer: {row[key]!r}") from None
    if minimum is not None and value < minimum:
        raise RowError(f"{key} is below {minimum}: {value}")
    return value


def _timestamp(row: dict, key: str) -> datetime:
    try:
        return datetime.strptime(row[key], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        raise RowError(f"{key} is not a timestamp: {row[key]!r}") from None


def _one_of(row: dict, key: str, allowed: tuple[str, ...]) -> str:
    if row[key] not in allowed:
        raise RowError(f"{key} {row[key]!r} is not one of {', '.join(allowed)}")
    return row[key]


def _text(row: dict, key: str) -> str:
    if not row[key]:
        raise RowError(f"{key} is empty")
    return row[key]


def _merchant(row: dict) -> tuple:
    return (
        _text(row, "merchant_id"),
        _text(row, "merchant_name"),
        _text(row, "country"),
        _one_of(row, "currency", CURRENCIES),
        _int(row, "fee_bps", minimum=0),
        _int(row, "fee_fixed_minor", minimum=0),
        _timestamp(row, "updated_at"),
    )


def _event(row: dict) -> tuple:
    return (
        _text(row, "event_id"),
        _text(row, "payment_id"),
        _text(row, "merchant_id"),
        _one_of(row, "event_type", EVENT_TYPES),
        _int(row, "sequence_no", minimum=1),
        _timestamp(row, "event_time"),
        _timestamp(row, "ingestion_time"),
        _int(row, "amount_minor", minimum=0),
        _one_of(row, "currency", CURRENCIES),
    )


def _settlement(row: dict) -> tuple:
    gross, fee, net = _int(row, "gross_minor"), _int(row, "fee_minor", minimum=0), _int(row, "net_minor")
    if net != gross - fee:
        raise RowError(f"net_minor {net} is not gross_minor - fee_minor ({gross - fee})")
    try:
        settled = date.fromisoformat(row["settlement_date"])
    except (TypeError, ValueError):
        raise RowError(f"settlement_date is not a date: {row['settlement_date']!r}") from None
    return (
        _text(row, "settlement_line_id"),
        settled,
        _text(row, "payment_reference"),
        _one_of(row, "line_type", LINE_TYPES),
        _one_of(row, "currency", CURRENCIES),
        gross,
        fee,
        net,
    )


TARGETS: dict[str, tuple[str, Callable[[dict], tuple]]] = {
    "merchants": ("raw.merchant", _merchant),
    "events": ("raw.payment_event", _event),
    "settlement": ("raw.settlement_line", _settlement),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_manifest(folder: Path) -> dict:
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        raise LoadError(f"{folder.name}: no manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = folder / entry["file"]
        if not path.exists():
            raise LoadError(f"{entry['file']}: listed in the manifest but missing")
        if _sha256(path) != entry["sha256"]:
            raise LoadError(f"{entry['file']}: checksum does not match the manifest")
        with path.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
        if header != FILE_COLUMNS[entry["kind"]]:
            raise LoadError(f"{entry['file']}: columns {header} do not match the contract")
    return manifest


def load_arrival(con: psycopg.Connection, folder: Path) -> list[FileResult]:
    manifest = _validate_manifest(folder)
    arrival = date.fromisoformat(manifest["arrival_date"])
    results = []
    with con.transaction():
        for entry in manifest["files"]:
            name, kind, checksum = entry["file"], entry["kind"], entry["sha256"]
            seen = con.execute(
                "select 1 from ops.file where file_name = %s and sha256 = %s", [name, checksum]
            )
            if seen.fetchone():
                results.append(FileResult(name, 0, 0, skipped=True))
                continue
            table, parse = TARGETS[kind]
            good, bad = [], []
            with (folder / name).open(newline="", encoding="utf-8") as handle:
                for number, row in enumerate(csv.DictReader(handle), start=1):
                    try:
                        good.append((number, *parse(row)))
                    except RowError as error:
                        bad.append((number, str(error), row))
            if len(good) + len(bad) != entry["rows"]:
                raise LoadError(f"{name}: read {len(good) + len(bad)} rows, manifest says {entry['rows']}")
            file_id = con.execute(
                "insert into ops.file (arrival_date, file_name, kind, sha256, row_count, loaded_rows, "
                "quarantined_rows) values (%s, %s, %s, %s, %s, %s, %s) returning file_id",
                [arrival, name, kind, checksum, entry["rows"], len(good), len(bad)],
            ).fetchone()[0]
            with con.cursor() as cur, cur.copy(f"copy {table} from stdin") as copy:
                for values in good:
                    copy.write_row((file_id, *values))
            for number, reason, row in bad:
                con.execute(
                    "insert into ops.quarantine (file_id, row_number, stage, reason, raw_row) "
                    "values (%s, %s, 'load', %s, %s)",
                    [file_id, number, reason, Jsonb(row)],
                )
            results.append(FileResult(name, len(good), len(bad), skipped=False))
    return results
