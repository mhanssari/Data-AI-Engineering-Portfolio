"""Data contracts: what a data product promises its consumers, checked against the live database and against its last release.

A contract is a YAML file in contracts/. contracts/released/ holds the last released version of each. Weakening a promise
without a new major version fails, so a consumer can trust that a 1.x contract still means what it did at 1.0.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import yaml
from psycopg import sql

from p2p_recon.config import CASE_DIR
from p2p_recon.db import connect, pipeline_run

CONTRACTS_DIR = CASE_DIR / "contracts"
RELEASED_DIR = CONTRACTS_DIR / "released"
SENSITIVITIES = ("internal", "confidential")
# The synthetic batches have fixed dates, so freshness is judged against this clock, shortly after batch 2, and never
# against the wall clock, which would make the data stale for ever. Every freshness check in the case uses it.
REFERENCE_CLOCK = datetime(2026, 10, 3, 6, 0, tzinfo=UTC)


def load_contract(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_contracts(directory: Path = CONTRACTS_DIR) -> dict[str, dict]:
    return {path.stem: load_contract(path) for path in sorted(directory.glob("*.yaml"))}


def _identifier(table: str) -> sql.Identifier:
    schema, name = table.split(".")
    return sql.Identifier(schema, name)


def _major(version: str) -> int:
    return int(version.split(".")[0])


def validate_shape(contract: dict) -> list[str]:
    """Is the file itself a complete contract? Runs without a database."""
    problems = []
    for field in ("name", "version", "owner", "consumers", "tables", "quality", "freshness", "access"):
        if not contract.get(field):
            problems.append(f"missing {field}")
    version = str(contract.get("version", ""))
    if len(version.split(".")) != 3 or not all(part.isdigit() for part in version.split(".")):
        problems.append(f"version {version!r} is not MAJOR.MINOR.PATCH")
    for table, spec in contract.get("tables", {}).items():
        for field in ("grain", "key", "columns"):
            if not spec.get(field):
                problems.append(f"{table}: missing {field}")
        for column in spec.get("key", []):
            if column not in spec.get("columns", {}):
                problems.append(f"{table}: key column {column} is not declared")
    return problems


def _check_schema(conn: psycopg.Connection, table: str, spec: dict) -> list[str]:
    schema, name = table.split(".")
    live = {
        row["column_name"]: row
        for row in conn.execute(
            "select column_name, data_type, is_nullable from information_schema.columns where table_schema = %s and table_name = %s",
            (schema, name),
        )
    }
    if not live:
        return [f"{table}: table does not exist"]
    problems = []
    for column, declared in spec["columns"].items():
        if column not in live:
            problems.append(f"{table}.{column}: declared but missing")
            continue
        if live[column]["data_type"] != declared["type"]:
            problems.append(
                f"{table}.{column}: type is {live[column]['data_type']}, contract says {declared['type']}"
            )
        if (live[column]["is_nullable"] == "YES") != declared["nullable"]:
            problems.append(
                f"{table}.{column}: nullable is {live[column]['is_nullable']}, contract says {declared['nullable']}"
            )
    # An added column is not breaking, but an undeclared one has no promise behind it, so the contract must catch up.
    problems += [
        f"{table}.{column}: exists but is not declared" for column in live if column not in spec["columns"]
    ]

    primary_key = [
        row["attname"]
        for row in conn.execute(
            """
            select a.attname from pg_index i
            join pg_attribute a on a.attrelid = i.indrelid and a.attnum = any(i.indkey)
            where i.indrelid = %s::regclass and i.indisprimary
            """,
            (table,),
        )
    ]
    if sorted(primary_key) != sorted(spec["key"]):
        problems.append(f"{table}: primary key is {sorted(primary_key)}, contract says {sorted(spec['key'])}")
    return problems


def _check_quality(conn: psycopg.Connection, check: dict) -> str | None:
    table = _identifier(check["table"])
    kind = check["check"]
    label = f"{check['table']}: {kind}"
    if kind == "min_rows":
        count = conn.execute(sql.SQL("select count(*) as n from {}").format(table)).fetchone()["n"]
        return (
            None if count >= check["min"] else f"{label}: {count} rows, contract says at least {check['min']}"
        )
    column = sql.Identifier(check["column"])
    label += f" {check['column']}"
    if kind == "max_null_rate":
        rate = conn.execute(
            sql.SQL(
                "select coalesce(avg(case when {} is null then 1.0 else 0.0 end), 0) as r from {}"
            ).format(column, table)
        ).fetchone()["r"]
        return (
            None
            if rate <= check["max"]
            else f"{label}: null rate {rate:.4f}, contract says at most {check['max']}"
        )
    if kind == "accepted_values":
        rows = conn.execute(
            sql.SQL(
                "select distinct {} as v from {} where {} is not null and not ({}::text = any(%s))"
            ).format(column, table, column, column),
            (check["values"],),
        ).fetchall()
        return f"{label}: unexpected {sorted(str(r['v']) for r in rows)}" if rows else None
    if kind == "non_negative":
        count = conn.execute(
            sql.SQL("select count(*) as n from {} where {} < 0").format(table, column)
        ).fetchone()["n"]
        return f"{label}: {count} negative values" if count else None
    raise ValueError(f"Unknown quality check {kind}")


def _check_access(conn: psycopg.Connection, table: str, access: dict) -> list[str]:
    problems = []
    for role in access["roles_may_read"]:
        if not conn.execute("select has_table_privilege(%s, %s, 'select') as ok", (role, table)).fetchone()[
            "ok"
        ]:
            problems.append(f"{table}: {role} should be able to read it and cannot")
    for role in access.get("roles_may_not_read", []):
        if conn.execute("select has_table_privilege(%s, %s, 'select') as ok", (role, table)).fetchone()["ok"]:
            problems.append(f"{table}: {role} should not be able to read it and can")
    if table in access.get("row_level_security", []):
        enabled = conn.execute(
            "select relrowsecurity as on_ from pg_class where oid = %s::regclass", (table,)
        ).fetchone()["on_"]
        if not enabled:
            problems.append(f"{table}: row-level security is promised and is off")
    return problems


def _live_columns(conn: psycopg.Connection, table: str) -> set[str]:
    schema, name = table.split(".")
    rows = conn.execute(
        "select column_name from information_schema.columns where table_schema = %s and table_name = %s",
        (schema, name),
    )
    return {row["column_name"] for row in rows}


def validate_data(conn: psycopg.Connection, contract: dict) -> list[str]:
    """Does the database honour this contract right now?"""
    problems = []
    for table, spec in contract["tables"].items():
        problems += _check_schema(conn, table, spec)
        problems += _check_access(conn, table, contract["access"])
    for check in contract["quality"]:
        # A column that has gone is already reported above, and querying it would only end the check with an error.
        if "column" in check and check["column"] not in _live_columns(conn, check["table"]):
            continue
        problem = _check_quality(conn, check)
        if problem:
            problems.append(problem)
    return problems


def check_freshness(conn: psycopg.Connection, contract: dict, as_of: datetime) -> list[str]:
    """Hours since the source extract, against the SLA. The synthetic data has fixed dates, so as_of is always supplied."""
    freshness = contract["freshness"]
    latest = conn.execute(
        "select max(extracted_at) as t from ops.ingest_file where source_system = any(%s)",
        (freshness["source_systems"],),
    ).fetchone()["t"]
    if latest is None:
        return [f"{contract['name']}: nothing ingested yet"]
    age = as_of - latest
    if age > timedelta(hours=freshness["sla_hours"]):
        return [
            f"{contract['name']}: data is {age.total_seconds() / 3600:.1f} hours old, SLA is {freshness['sla_hours']}"
        ]
    return []


def breaking_changes(released: dict, current: dict) -> list[str]:
    """What a consumer of the released contract would be hurt by. Additions and tightenings are not here, on purpose."""
    changes = []
    for table, old in released["tables"].items():
        new = current["tables"].get(table)
        if new is None:
            changes.append(f"table removed: {table}")
            continue
        if sorted(old["key"]) != sorted(new["key"]):
            changes.append(f"key changed: {table}")
        if old["grain"] != new["grain"]:
            changes.append(f"grain changed: {table}")
        for column, spec in old["columns"].items():
            if column not in new["columns"]:
                changes.append(f"column removed: {table}.{column}")
            elif spec["type"] != new["columns"][column]["type"]:
                changes.append(f"type changed: {table}.{column}")
            elif not spec["nullable"] and new["columns"][column]["nullable"]:
                changes.append(f"now nullable: {table}.{column}")

    def by_key(contract: dict) -> dict:
        return {(c["table"], c["check"], c.get("column")): c for c in contract["quality"]}

    new_checks = by_key(current)
    for key, old in by_key(released).items():
        new = new_checks.get(key)
        if new is None:
            changes.append(f"quality check removed: {key}")
        elif "min" in old and new["min"] < old["min"] or "max" in old and new["max"] > old["max"]:
            changes.append(f"threshold weakened: {key}")
        elif "values" in old and set(new["values"]) - set(old["values"]):
            changes.append(f"new accepted values: {key}")
    if current["freshness"]["sla_hours"] > released["freshness"]["sla_hours"]:
        changes.append("freshness SLA weakened")
    return changes


def check_release(released: dict, current: dict) -> list[str]:
    """Breaking changes need a new major version; any change at all needs a new version."""
    problems = []
    changes = breaking_changes(released, current)
    if changes and _major(current["version"]) <= _major(released["version"]):
        problems += [
            f"{current['name']}: breaking without a major version bump: {change}" for change in changes
        ]
    if current != released and current["version"] == released["version"]:
        problems.append(f"{current['name']}: changed but still at version {current['version']}")
    return problems


def record_check(as_of: datetime = REFERENCE_CLOCK) -> dict:
    """check_all, with one ops.dq_result row per contract under pipeline step contracts_check, for the admin pages."""
    with pipeline_run("contracts_check", {"as_of": as_of.isoformat()}) as run, connect() as conn:
        results = check_all(conn, as_of)
        for name, result in results.items():
            conn.execute(
                "insert into ops.dq_result (run_id, check_name, scope, expected, actual, status, detail) "
                "values (%s, %s, %s, 0, %s, %s, %s)",
                (
                    run.run_id,
                    f"contract.{name}",
                    str(result["version"]),
                    len(result["problems"]),
                    "fail" if result["problems"] else "pass",
                    "; ".join(result["problems"]) or None,
                ),
            )
        run.counts = {
            "contracts": len(results),
            "with_problems": sum(1 for r in results.values() if r["problems"]),
        }
    return results


def check_all(conn: psycopg.Connection, as_of: datetime | None = None) -> dict:
    results = {}
    for name, contract in load_contracts().items():
        problems = validate_shape(contract)
        problems += validate_data(conn, contract)
        released_path = RELEASED_DIR / f"{name}.yaml"
        if released_path.exists():
            problems += check_release(load_contract(released_path), contract)
        else:
            problems.append(f"{name}: no released version in contracts/released")
        if as_of is not None:
            problems += check_freshness(conn, contract, as_of)
        results[name] = {"version": contract.get("version"), "problems": problems}
    return results
