"""Connections and migrations. Connection settings come from libpq's standard environment variables (PGHOST,
PGPORT, PGDATABASE, PGUSER, PGPASSWORD); nothing is hard-coded and no password is ever written to a file."""

from pathlib import Path

import psycopg

from payrecon.config import CASE_ROOT
from payrecon.sql_files import read_sql

MIGRATIONS = CASE_ROOT / "sql" / "migrations"


def connect(dbname: str | None = None) -> psycopg.Connection:
    """Autocommit, so each unit of work is an explicit `with con.transaction()` block and never nests silently
    inside an implicit transaction left open by an earlier query."""
    kwargs = {"dbname": dbname} if dbname else {}
    return psycopg.connect(autocommit=True, **kwargs)


def migrate(con: psycopg.Connection, folder: Path = MIGRATIONS) -> list[str]:
    """Apply each migration once, in name order, each in its own transaction."""
    con.execute(
        "create table if not exists public.schema_migration (version text primary key, "
        "applied_at timestamptz not null default now())"
    )
    applied = {row[0] for row in con.execute("select version from public.schema_migration")}
    done = []
    for path in sorted(folder.glob("*.sql")):
        if path.stem in applied:
            continue
        with con.transaction():
            con.execute(read_sql(path))
            con.execute("insert into public.schema_migration (version) values (%s)", [path.stem])
        done.append(path.stem)
    return done


def reset(con: psycopg.Connection) -> None:
    """Drop every schema this case owns; migrations recreate them."""
    with con.transaction():
        for schema in ("stream", "recon", "core", "raw", "ops"):
            con.execute(f"drop schema if exists {schema} cascade")
        con.execute("drop table if exists public.schema_migration")
