import csv
from pathlib import Path

import pytest

from loyalty import generator


@pytest.fixture(scope="session")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Both batches from the default seed, written once per test session."""
    out = tmp_path_factory.mktemp("generated")
    generator.generate(out)
    return out


def read_rows(batch_dir: Path, table: str) -> list[dict[str, str]]:
    with (batch_dir / f"{table}.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
