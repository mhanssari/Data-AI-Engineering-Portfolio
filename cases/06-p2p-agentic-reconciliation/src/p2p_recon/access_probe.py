"""Try each role against what it may and may not do, and store what PostgreSQL did: `uv run p2p access-probe`.

The pytest security suite proves the same rules but leaves no record behind. This probe runs against the working
database, so the admin Access page can show when access was last tested and how it went. Every attempt runs as the role
itself, inside a transaction that is rolled back, so nothing it tries can change data. Expectations come from the data
contracts' access promises and from the fixed rules in docs/security.md; nothing here reads the grants to decide what to
expect.
"""

from dataclasses import dataclass

import psycopg
from psycopg import sql

from p2p_recon import contracts, evaluation
from p2p_recon.db import connect, pipeline_run

PEOPLE = ("ap_analyst_uk", "ap_analyst_de", "controller", "auditor")
ROLES = (*PEOPLE, "agent_service", "admin_reviewer")
OTHER_COMPANY = {"ap_analyst_uk": "DE01", "ap_analyst_de": "GB01"}


@dataclass(frozen=True)
class Attempt:
    check: str
    role: str
    statement: str
    expect: str  # allowed, denied, or no_rows: allowed but returning nothing


def attempts() -> list[Attempt]:
    found = []
    for contract in contracts.load_contracts().values():
        access = contract["access"]
        for table in contract["tables"]:
            count = f"select count(*) as n from {table}"
            found += [Attempt(f"read {table}", role, count, "allowed") for role in access["roles_may_read"]]
            found += [
                Attempt(f"read {table}", role, count, "denied")
                for role in access.get("roles_may_not_read", [])
            ]
            found.append(Attempt(f"read {table}", "admin_reviewer", count, "allowed"))
    for role in ROLES:
        found += [
            Attempt(
                "read a bank account number",
                role,
                "select account_number from core.supplier_bank_account limit 1",
                "denied",
            ),
            Attempt(
                "read raw landing data", role, "select count(*) as n from raw.sap_invoice_header", "denied"
            ),
            Attempt(
                "change an invoice",
                role,
                "update core.supplier_invoice set is_deleted = is_deleted where false",
                "denied",
            ),
            Attempt(
                "write the audit log directly",
                role,
                "insert into agent.audit_log (event, session_user_name, role_in_use, detail) "
                "select 'human_decision', 'probe', 'probe', '{}' where false",
                "denied",
            ),
        ]
    for role in ("auditor", "agent_service", "admin_reviewer"):
        found.append(
            Attempt(
                "record a decision",
                role,
                "insert into agent.human_decision (proposal_id, decision, decided_by) "
                "select proposal_id, 'ACCEPT', current_user from agent.proposal where false",
                "denied",
            )
        )
    for role, company in OTHER_COMPANY.items():
        found.append(
            Attempt(
                f"see {company} invoices",
                role,
                f"select count(*) as n from core.supplier_invoice where company_code = '{company}'",
                "no_rows",
            )
        )
    return found


def _try(conn: psycopg.Connection, attempt: Attempt) -> tuple[str, int | None, str | None]:
    try:
        with conn.transaction(force_rollback=True):
            conn.execute(sql.SQL("set local role {}").format(sql.Identifier(attempt.role)))
            cursor = conn.execute(attempt.statement)
            row = cursor.fetchone() if cursor.description else None
            return "allowed", (row or {}).get("n"), None
    except psycopg.errors.InsufficientPrivilege as exc:
        return "denied", None, exc.diag.message_primary
    except psycopg.Error as exc:
        return "error", None, f"{type(exc).__name__}: {exc.diag.message_primary}"


def _passed(attempt: Attempt, outcome: str, rows: int | None) -> bool:
    if attempt.expect == "no_rows":
        return outcome == "allowed" and rows == 0
    return outcome == attempt.expect


def run_probe() -> dict:
    results = []
    with pipeline_run("access_probe") as run, connect() as conn:
        for attempt in attempts():
            outcome, rows, error = _try(conn, attempt)
            results.append((attempt, outcome, rows, error, _passed(attempt, outcome, rows)))
        run.counts = {"attempts": len(results), "failed": sum(1 for *_, passed in results if not passed)}
    evaluation_run_id = evaluation.record(
        "access_probe",
        [
            (
                "access",
                f"{attempt.role} · {attempt.check}",
                "outcome",
                rows,
                passed,
                {
                    "statement": attempt.statement,
                    "expected": attempt.expect,
                    "outcome": outcome,
                    "error": error,
                },
            )
            for attempt, outcome, rows, error, passed in results
        ],
        parameters={"pipeline_run_id": run.run_id},
    )
    return {"evaluation_run_id": evaluation_run_id, **run.counts}
