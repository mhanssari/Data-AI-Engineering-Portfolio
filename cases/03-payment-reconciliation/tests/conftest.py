"""Shared fixtures.

- `generated`: the full seeded delivery, written once per session (about 15 seconds).
- `con`: a connection to a fresh, migrated database created for one test and dropped after it, so tests never
  touch the working database and never depend on each other.
- `delivered`: a fresh database per test module with the whole generated delivery loaded, for tests that only
  build and read from it.

Database fixtures need the case's PostgreSQL; connection settings come from libpq's PG* variables, as everywhere
else in the case.
"""

import csv
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest

from payrecon import db, generator, loader
from payrecon.config import FILE_COLUMNS, arrival_days, file_kind

# The state machine as written in docs/data-model.md: (state, event type) -> new state. 'new' is before any event.
# Transcribed by hand, so a test comparing it with core.allowed_transition catches drift between docs and code.
TRANSITIONS = {
    ("new", "initiated"): "initiated",
    ("initiated", "authorised"): "authorised",
    ("initiated", "declined"): "declined",
    ("authorised", "captured"): "captured",
    ("authorised", "voided"): "voided",
    ("authorised", "expired"): "expired",
    ("captured", "refunded"): "refunded",
    ("refunded", "refunded"): "refunded",
    ("captured", "reversed"): "reversed",
}


@pytest.fixture(scope="session")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("generated")
    generator.generate(out)
    return out


@contextmanager
def fresh_database(name: str) -> Iterator[psycopg.Connection]:
    """Create a migrated database, yield a connection to it, and drop it afterwards."""
    try:
        admin = psycopg.connect(autocommit=True)
    except psycopg.OperationalError as error:
        pytest.skip(f"no PostgreSQL to test against (start compose.yaml's postgres): {error}")
    with admin:
        admin.execute(f"drop database if exists {name} with (force)")
        admin.execute(f"create database {name}")
        try:
            with db.connect(name) as connection:
                db.migrate(connection)
                yield connection
        finally:
            admin.execute(f"drop database if exists {name} with (force)")


@pytest.fixture
def con() -> Iterator[psycopg.Connection]:
    with fresh_database(f"payrecon_test_{os.getpid()}") as connection:
        yield connection


@pytest.fixture(scope="module")
def delivered(generated: Path) -> Iterator[psycopg.Connection]:
    with fresh_database(f"payrecon_test_delivered_{os.getpid()}") as connection:
        for day in arrival_days():
            loader.load_arrival(connection, generated / f"arrival_{day}")
        yield connection


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
