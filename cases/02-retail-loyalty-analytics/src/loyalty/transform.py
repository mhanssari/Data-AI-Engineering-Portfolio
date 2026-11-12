"""Run dbt against a given warehouse file."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from loyalty.config import CASE_ROOT

DBT_DIR = CASE_ROOT / "dbt"


class DbtError(Exception):
    """A dbt command failed; its log says which node and why."""


def ensure_profile() -> None:
    """Create profiles.yml from the example on first use; the DuckDB profile holds no secrets."""
    profile = DBT_DIR / "profiles.yml"
    if not profile.exists():
        shutil.copyfile(DBT_DIR / "profiles.example.yml", profile)


def _dbt_executable() -> str:
    scripts = Path(sys.executable).parent
    for name in ("dbt.exe", "dbt"):
        if (scripts / name).exists():
            return str(scripts / name)
    raise DbtError(f"dbt is not installed next to {sys.executable}; run `uv sync`")


def run_dbt(args: list[str], warehouse: Path) -> None:
    """Run one dbt command in its own process.

    A separate process, not dbt's in-process runner: dbt-duckdb keeps the database file open for the life of the
    process, and DuckDB allows one writer, so the next load could not open the file.
    """
    ensure_profile()
    # The profile reads the warehouse path from the environment, so every command targets the same file.
    env = {**os.environ, "LOYALTY_WAREHOUSE": str(warehouse.resolve())}
    command = [_dbt_executable(), *args, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
    if subprocess.run(command, env=env, check=False).returncode != 0:
        raise DbtError(f"dbt {' '.join(args)} failed")
