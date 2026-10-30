"""Land one delivered batch in raw, unchanged and atomically.

The manifest is the delivery contract: a file that disagrees with its checksum or row count, or whose header differs
from the raw table, fails the whole batch before anything is committed. A file already landed with the same checksum
is skipped, which is what makes re-delivering a batch a no-op.
"""

import csv
import hashlib
import json
import re
from io import StringIO
from pathlib import Path

import psycopg

from p2p_recon.db import Run, connect, pipeline_run

METADATA_COLUMNS = {
    "raw_id",
    "run_id",
    "source_system",
    "batch_id",
    "source_file",
    "file_row",
    "extracted_at",
    "cdc_op",
}


class DeliveryError(ValueError):
    """The delivered file does not match its manifest or the raw table."""


def _business_columns(conn: psycopg.Connection, table: str) -> list[str]:
    rows = conn.execute(
        "select column_name from information_schema.columns where table_schema = 'raw' and table_name = %s "
        "order by ordinal_position",
        (table,),
    ).fetchall()
    if not rows:
        raise DeliveryError(f"Unknown raw table: {table}")
    return [r["column_name"] for r in rows if r["column_name"] not in METADATA_COLUMNS]


def _land_file(conn: psycopg.Connection, run: Run, manifest: dict, entry: dict, batch_dir: Path) -> int:
    table = entry["table"]
    if not re.fullmatch(r"[a-z_]+", table):
        raise DeliveryError(f"Invalid table name in manifest: {table}")
    path = batch_dir / entry["path"]
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    if checksum != entry["sha256"]:
        raise DeliveryError(f"{entry['path']}: checksum differs from the manifest")

    already = conn.execute(
        "select 1 from ops.ingest_file where source_system = %s and file_name = %s and checksum = %s",
        (entry["source_system"], f"{manifest['batch_id']}/{entry['path']}", checksum),
    ).fetchone()
    if already:
        return 0

    reader = csv.reader(StringIO(content.decode("utf-8")), delimiter=entry["delimiter"])
    header = next(reader)
    expected = _business_columns(conn, table)
    business_header = [c for c in header if c != "cdc_op"]
    if business_header != expected:
        raise DeliveryError(
            f"{entry['path']}: header differs from raw.{table}: {business_header} != {expected}"
        )

    meta = ("run_id", "source_system", "batch_id", "source_file", "file_row", "extracted_at")
    meta_values = (run.run_id, entry["source_system"], manifest["batch_id"], entry["path"])
    columns = ", ".join((*meta, *header))
    count = 0
    with conn.cursor().copy(f"copy raw.{table} ({columns}) from stdin") as copy:
        for count, row in enumerate(reader, start=1):
            if len(row) != len(header):
                raise DeliveryError(f"{entry['path']} row {count}: {len(row)} fields, expected {len(header)}")
            # An empty field is a null: source extracts have no way to distinguish the two.
            copy.write_row((*meta_values, count, manifest["extracted_at"], *[value or None for value in row]))
    if count != entry["row_count"]:
        raise DeliveryError(f"{entry['path']}: {count} rows landed, manifest declares {entry['row_count']}")

    conn.execute(
        "insert into ops.ingest_file (source_system, file_name, checksum, batch_id, extracted_at, row_count, run_id) "
        "values (%s, %s, %s, %s, %s, %s, %s)",
        (
            entry["source_system"],
            f"{manifest['batch_id']}/{entry['path']}",
            checksum,
            manifest["batch_id"],
            manifest["extracted_at"],
            count,
            run.run_id,
        ),
    )
    run.lineage(conn, f"file:{manifest['batch_id']}/{entry['path']}", f"raw.{table}", count)
    return count


def ingest_batch(batch_dir: Path) -> dict:
    manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
    with pipeline_run("ingest", {"batch_id": manifest["batch_id"]}) as run, connect() as conn:
        landed = {
            entry["table"]: _land_file(conn, run, manifest, entry, batch_dir) for entry in manifest["files"]
        }
        run.counts = {
            "rows_landed": sum(landed.values()),
            "files_new": sum(1 for n in landed.values() if n),
            "files_skipped": sum(1 for n in landed.values() if not n),
        }
        return run.counts
