"""Append-only landing of batch extracts into the `raw` schema.

Every file is checked against its manifest (checksum, row count) and against the column contract in `config.py`
before anything is written. The whole batch then lands in one transaction, so a failure leaves nothing behind.
A file whose checksum is already in `raw._load_log` is skipped: loading the same batch twice lands nothing.
Batches must arrive in order, because the member snapshot records state as of each batch.
"""

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from loyalty.config import SOURCE_COLUMNS

METADATA_COLUMNS = (
    "_load_id bigint, _batch_id integer, _source_file varchar, _file_checksum varchar, _loaded_at timestamp"
)


class LoadError(Exception):
    """The batch was rejected; nothing was written."""


@dataclass(frozen=True)
class FileResult:
    table: str
    rows: int
    skipped: bool


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle), [])


def _ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("create schema if not exists raw")
    con.execute(
        "create table if not exists raw._load_log ("
        "load_id bigint primary key, batch_id integer, table_name varchar, source_file varchar, "
        "file_checksum varchar unique, row_count bigint, loaded_at timestamp)"
    )
    for table, columns in SOURCE_COLUMNS.items():
        # Raw keeps values exactly as delivered; typing belongs to staging, where a bad value is visible and tested.
        column_sql = ", ".join(f"{column} varchar" for column in columns)
        con.execute(f"create table if not exists raw.{table} ({column_sql}, {METADATA_COLUMNS})")


def _validate(batch_dir: Path) -> dict:
    manifest_path = batch_dir / "manifest.json"
    if not manifest_path.exists():
        raise LoadError(f"{batch_dir}: no manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tables = {entry["table"] for entry in manifest["files"]}
    if tables != set(SOURCE_COLUMNS):
        raise LoadError(
            f"batch {manifest['batch_id']}: manifest lists {sorted(tables)}, expected {sorted(SOURCE_COLUMNS)}"
        )
    for entry in manifest["files"]:
        path = batch_dir / entry["file"]
        if not path.exists():
            raise LoadError(f"{path.name}: missing")
        if _sha256(path) != entry["sha256"]:
            raise LoadError(
                f"{path.name}: checksum does not match the manifest; the file changed after it was written"
            )
        header = _header(path)
        if header != SOURCE_COLUMNS[entry["table"]]:
            raise LoadError(
                f"{path.name}: columns {header} do not match the contract {SOURCE_COLUMNS[entry['table']]}"
            )
    return manifest


def load_batch(con: duckdb.DuckDBPyConnection, batch_dir: Path) -> list[FileResult]:
    manifest = _validate(batch_dir)
    batch_id = int(manifest["batch_id"])
    _ensure_schema(con)

    loaded_batches = {row[0] for row in con.execute("select distinct batch_id from raw._load_log").fetchall()}
    missing = [b for b in range(1, batch_id) if b not in loaded_batches]
    if missing:
        raise LoadError(
            f"batch {batch_id}: earlier batches {missing} are not loaded; batches must arrive in order"
        )

    results: list[FileResult] = []
    loaded_at = datetime.now().replace(microsecond=0)
    con.execute("begin transaction")
    try:
        for entry in manifest["files"]:
            table, checksum = entry["table"], entry["sha256"]
            if con.execute("select 1 from raw._load_log where file_checksum = ?", [checksum]).fetchone():
                results.append(FileResult(table, 0, skipped=True))
                continue
            load_id = con.execute("select coalesce(max(load_id), 0) + 1 from raw._load_log").fetchone()[0]
            columns = SOURCE_COLUMNS[table]
            column_list = ", ".join(columns)
            # Explicit columns, no type sniffing: every value lands as the text that was delivered.
            column_types = ", ".join(f"'{column}': 'varchar'" for column in columns)
            path = (batch_dir / entry["file"]).as_posix()
            con.execute(
                f"insert into raw.{table} "
                f"select {column_list}, ?, ?, ?, ?, ? "
                f"from read_csv(?, header = true, auto_detect = false, columns = {{{column_types}}})",
                [load_id, batch_id, entry["file"], checksum, loaded_at, path],
            )
            landed = con.execute(
                f"select count(*) from raw.{table} where _load_id = ?", [load_id]
            ).fetchone()[0]
            if landed != entry["rows"]:
                raise LoadError(f"{entry['file']}: landed {landed} rows, manifest says {entry['rows']}")
            con.execute(
                "insert into raw._load_log values (?, ?, ?, ?, ?, ?, ?)",
                [load_id, batch_id, table, entry["file"], checksum, landed, loaded_at],
            )
            results.append(FileResult(table, landed, skipped=False))
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise
    return results


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))
