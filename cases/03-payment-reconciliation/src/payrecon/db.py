"""Connections and migrations. Connection settings come from libpq's standard environment variables (PGHOST,
PGPORT, PGDATABASE, PGUSER, PGPASSWORD); nothing is hard-coded and no password is ever written to a file."""

from pathlib import Path

import psycopg

from payrecon.config import CASE_ROOT

MIGRATIONS = CASE_ROOT / "sql" / "migrations"


def connect(dbname: str | None = None) -> psycopg.Connection:
    return psycopg.connect(dbname=dbname) if dbname else psycopg.connect()


def migrate(con: psycopg.Connection, folder: Path = MIGRATIONS) -> list[str]:
    """Apply each migration once, in name order, each in its own transaction."""
    con.execute(
        "create table if not exists public.schema_migration (version text primary key, "
        "applied_at timestamptz not null default now())"
    )
    con.commit()
    applied = {row[0] for row in con.execute("select version from public.schema_migration")}
    done = []
    for path in sorted(folder.glob("*.sql")):
        if path.stem in applied:
            continue
        with con.transaction():
            con.execute(path.read_text(encoding="utf-8"))
            con.execute("insert into public.schema_migration (version) values (%s)", [path.stem])
        done.append(path.stem)
    return done


def reset(con: psycopg.Connection) -> None:
    """Drop every schema this case owns; migrations recreate them."""
    with con.transaction():
        for schema in ("recon", "core", "raw", "ops"):
            con.execute(f"drop schema if exists {schema} cascade")
        con.execute("drop table if exists public.schema_migration")
