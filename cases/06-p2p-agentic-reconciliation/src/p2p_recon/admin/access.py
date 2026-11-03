"""Who may do what, read from the PostgreSQL catalogue rather than from documentation, and what was done, from the audit
log and the admin view log."""

from datetime import date

import psycopg

from p2p_recon.access_probe import ROLES

SCHEMAS = ("raw", "stg", "core", "mdm", "recon", "docs", "agent", "ops")
# Column grants that matter more than the table's: the bank number is revoked even where the table could be read.
SENSITIVE_COLUMNS = (("core.supplier_bank_account", "account_number"),)


def permission_matrix(conn: psycopg.Connection, schemas: tuple[str, ...] = SCHEMAS) -> list[dict]:
    """One row per table or view: row security, its policies, and each role's privileges as letters (S, I, U, D;
    lower case where the grant covers some columns only)."""
    granted = conn.execute(
        """
        select n.nspname || '.' || c.relname as name, case c.relkind when 'v' then 'view' else 'table' end as kind,
               c.relrowsecurity as row_security,
               (select string_agg(p.polname, ', ' order by p.polname) from pg_policy p where p.polrelid = c.oid) as policies,
               r.role,
               has_table_privilege(r.role, c.oid, 'SELECT') as s, has_table_privilege(r.role, c.oid, 'INSERT') as i,
               has_table_privilege(r.role, c.oid, 'UPDATE') as u, has_table_privilege(r.role, c.oid, 'DELETE') as d,
               has_any_column_privilege(r.role, c.oid, 'SELECT') as s_col,
               has_any_column_privilege(r.role, c.oid, 'INSERT') as i_col,
               has_any_column_privilege(r.role, c.oid, 'UPDATE') as u_col
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        cross join unnest(%s::text[]) as r(role)
        where n.nspname = any(%s) and c.relkind in ('r', 'v', 'p')
        order by 1
        """,
        (list(ROLES), list(schemas)),
    ).fetchall()
    rows: dict[str, dict] = {}
    for g in granted:
        row = rows.setdefault(g["name"], {k: g[k] for k in ("name", "kind", "row_security", "policies")})
        # Upper case: on the whole table. Lower case: on some columns only, such as the reviewer's insert on its notes.
        letters = ""
        for letter in ("S", "I", "U", "D"):
            if g[letter.lower()]:
                letters += letter
            elif g.get(f"{letter.lower()}_col"):
                letters += letter.lower()
        row[g["role"]] = letters or "–"
    return list(rows.values())


def column_grants(conn: psycopg.Connection) -> list[dict]:
    rows = []
    for table, column in SENSITIVE_COLUMNS:
        row = {"column": f"{table}.{column}"}
        for role in ROLES:
            row[role] = conn.execute(
                "select has_column_privilege(%s, %s, %s, 'SELECT') as ok", (role, table, column)
            ).fetchone()["ok"]
        rows.append(row)
    return rows


def policies(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        """
        select schemaname || '.' || tablename as table_name, policyname, cmd, roles::text[] as roles, qual, with_check
        from pg_policies where schemaname = any(%s) order by 1, 2
        """,
        (list(SCHEMAS),),
    ).fetchall()


def audit_events(conn: psycopg.Connection) -> list[str]:
    return [r["event"] for r in conn.execute("select distinct event from agent.audit_log order by 1")]


def audit_log(
    conn: psycopg.Connection,
    events: tuple[str, ...] = (),
    roles: tuple[str, ...] = (),
    date_from: date | None = None,
    date_to: date | None = None,
    proposal_id: int | None = None,
    limit: int = 500,
) -> list[dict]:
    """Newest first, filtered only by bound parameters."""
    return conn.execute(
        """
        select audit_id, occurred_at, event, role_in_use, on_behalf_of, session_user_name, agent_run_id, proposal_id, detail
        from agent.audit_log
        where (cardinality(%(events)s::text[]) = 0 or event = any(%(events)s))
          and (cardinality(%(roles)s::text[]) = 0 or role_in_use = any(%(roles)s))
          and (%(date_from)s::date is null or occurred_at >= %(date_from)s::date)
          and (%(date_to)s::date is null or occurred_at < %(date_to)s::date + 1)
          and (%(proposal)s::bigint is null or proposal_id = %(proposal)s)
        order by audit_id desc limit %(limit)s
        """,
        {
            "events": list(events),
            "roles": list(roles),
            "date_from": date_from,
            "date_to": date_to,
            "proposal": proposal_id,
            "limit": limit,
        },
    ).fetchall()


def view_log(conn: psycopg.Connection, limit: int = 200) -> list[dict]:
    return conn.execute(
        """
        select view_id, viewed_at, role_in_use, session_user_name, page, object_type, object_id from ops.admin_view_log
        order by view_id desc limit %s
        """,
        (limit,),
    ).fetchall()
