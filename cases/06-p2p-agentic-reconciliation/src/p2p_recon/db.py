"""PostgreSQL connections and pipeline-run bookkeeping.

Every CLI step runs inside a `pipeline_run` context: the run row is written on its own
autocommit connection, so a failed step is still recorded as failed even though the
step's own transaction rolls back and leaves no partial data behind.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from p2p_recon.config import get_settings


def connect(autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(get_settings().conninfo(), autocommit=autocommit, row_factory=dict_row)


def read_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class Run:
    """Handle for the current run: an id plus counters that end up in ops.pipeline_run."""

    def __init__(self, run_id: int):
        self.run_id = run_id
        self.counts: dict[str, object] = {}

    def lineage(self, conn: psycopg.Connection, source: str, target: str, rows: int) -> None:
        conn.execute(
            "insert into ops.lineage_edge (run_id, source_dataset, target_dataset, row_count) "
            "values (%s, %s, %s, %s)",
            (self.run_id, source, target, rows),
        )


@contextmanager
def pipeline_run(step: str, parameters: dict | None = None) -> Iterator[Run]:
    with connect(autocommit=True) as log:
        run_id = log.execute(
            "insert into ops.pipeline_run (step, status, parameters) values (%s, 'running', %s) "
            "returning run_id",
            (step, json.dumps(parameters or {}, default=str)),
        ).fetchone()["run_id"]
        run = Run(run_id)
        try:
            yield run
        except BaseException as exc:
            log.execute(
                "update ops.pipeline_run set status = 'failed', finished_at = now(), "
                "row_counts = %s, error_message = %s where run_id = %s",
                (json.dumps(run.counts, default=str), f"{type(exc).__name__}: {exc}", run_id),
            )
            raise
        log.execute(
            "update ops.pipeline_run set status = 'succeeded', finished_at = now(), row_counts = %s "
            "where run_id = %s",
            (json.dumps(run.counts, default=str), run_id),
        )
