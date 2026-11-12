"""Resolve explicit relative SQL includes so batch and stream share their deterministic state rules."""

import re
from pathlib import Path

from payrecon.config import CASE_ROOT


def read_sql(path: Path, parents: tuple[Path, ...] = ()) -> str:
    path = path.resolve()
    if not path.is_relative_to((CASE_ROOT / "sql").resolve()) or path in parents:
        raise ValueError("SQL include escapes the SQL directory or is recursive")
    return re.sub(
        r"^-- include: (.+)$",
        lambda match: read_sql(path.parent / match[1].strip(), (*parents, path)),
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
