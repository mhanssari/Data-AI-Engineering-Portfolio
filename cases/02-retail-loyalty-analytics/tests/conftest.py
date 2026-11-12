import csv
import shutil
from pathlib import Path

import duckdb
import pytest

from loyalty import generator, loader, transform


@pytest.fixture(scope="session")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Both batches from the default seed, written once per test session."""
    out = tmp_path_factory.mktemp("generated")
    generator.generate(out)
    return out


def read_rows(batch_dir: Path, table: str) -> list[dict[str, str]]:
    with (batch_dir / f"{table}.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def query(warehouse: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _load_and_build(generated: Path, warehouse: Path, batch: int) -> None:
    con = loader.connect(warehouse)
    try:
        loader.load_batch(con, generated / f"batch_{batch}")
    finally:
        con.close()
    transform.run_dbt(["build"], warehouse)


@pytest.fixture(scope="session")
def after_batches(generated: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Batches replayed in order: a copy of the warehouse after batch 1's build, and the warehouse after batch 2's."""
    warehouse = tmp_path_factory.mktemp("warehouse") / "loyalty.duckdb"
    _load_and_build(generated, warehouse, 1)
    # Same file name in another folder: dbt-duckdb names the catalog after the file, and the views refer to it.
    after_batch_1 = tmp_path_factory.mktemp("after_batch_1") / "loyalty.duckdb"
    shutil.copy(warehouse, after_batch_1)
    _load_and_build(generated, warehouse, 2)
    return after_batch_1, warehouse
