"""Minimal forward-only migration runner.

Applies sql/migrations/NNN_*.sql in order, once each, and records a SHA-256 of every
applied file. If an already-applied file is edited, the runner stops rather than
letting the database silently drift from the repository. sql/routines/*.sql (views and
functions) are then re-applied in name order.
"""

import hashlib

import psycopg
from psycopg import sql

from p2p_recon.config import SQL_DIR, get_settings
from p2p_recon.db import connect

MIGRATIONS_DIR = SQL_DIR / "migrations"
ROUTINES_DIR = SQL_DIR / "routines"

BOOTSTRAP = """
create schema if not exists ops;
create table if not exists ops.schema_migration (
    version     text primary key,
    checksum    text not null,
    applied_at  timestamptz not null default now()
);
"""


def migrate(conn: psycopg.Connection | None = None) -> list[str]:
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute(BOOTSTRAP)
        applied = {
            r["version"]: r["checksum"]
            for r in conn.execute("select version, checksum from ops.schema_migration").fetchall()
        }
        done: list[str] = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            body = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise RuntimeError(
                        f"{path.name} changed after it was applied; add a new migration instead"
                    )
                continue
            conn.execute(body)
            conn.execute(
                "insert into ops.schema_migration (version, checksum) values (%s, %s)",
                (path.name, checksum),
            )
            done.append(path.name)
        # Views and functions hold no data, so they are re-applied on every run rather than versioned.
        for path in sorted(ROUTINES_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
        # The password lives in the environment, never in a migration file.
        settings = get_settings()
        if not settings.app_db_password:
            raise RuntimeError("Set APP_DB_PASSWORD in the case .env (see .env.example)")
        conn.execute(
            sql.SQL("alter role {} login password {}").format(
                sql.Identifier(settings.app_db_user), sql.Literal(settings.app_db_password)
            )
        )
        conn.commit()
        return done
    finally:
        if own:
            conn.close()
