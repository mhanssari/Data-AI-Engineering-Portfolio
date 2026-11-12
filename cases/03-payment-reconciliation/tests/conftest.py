"""Shared fixtures.

- `generated`: the full seeded delivery, written once per session (about 15 seconds).
- `con`: a connection to a fresh, migrated database created for one test and dropped after it, so loader tests
  never touch the working database and never depend on each other. Needs the case's PostgreSQL; connection
  settings come from libpq's PG* variables, as everywhere else in the case.
"""

import csv
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from payrecon import db, generator
from payrecon.config import FILE_COLUMNS, file_kind

TEST_DATABASE = f"payrecon_test_{os.getpid()}"


@pytest.fixture(scope="session")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("generated")
    generator.generate(out)
    return out


@pytest.fixture
def con() -> Iterator[psycopg.Connection]:
    try:
        admin = psycopg.connect(autocommit=True)
    except psycopg.OperationalError as error:
        pytest.skip(f"no PostgreSQL to test against (start compose.yaml's postgres): {error}")
    with admin:
        admin.execute(f"drop database if exists {TEST_DATABASE} with (force)")
        admin.execute(f"create database {TEST_DATABASE}")
        try:
            with db.connect(TEST_DATABASE) as connection:
                db.migrate(connection)
                yield connection
        finally:
            admin.execute(f"drop database if exists {TEST_DATABASE} with (force)")


def write_arrival(folder: Path, arrival: str, files: dict[str, list[dict]]) -> Path:
    """Write a small arrival folder by hand, with a manifest that matches it."""
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, rows in files.items():
        kind = file_kind(name)
        path = folder / name
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FILE_COLUMNS[kind], lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        entries.append(
            {
                "file": name,
                "kind": kind,
                "rows": len(rows),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    (folder / "manifest.json").write_text(
        json.dumps({"arrival_date": arrival, "files": entries}), encoding="utf-8"
    )
    return folder
