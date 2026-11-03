"""The governance administrator reads across both companies, decides nothing and writes only review notes.

As in test_security.py, every check connects through the application login and the real role, with no application filter
in the SQL, so what passes is the database's doing. The superuser `conn` fixture only sets up data and gives true totals.
"""

import re

import psycopg
import pytest

from p2p_recon import graph, review
from p2p_recon.admin import notes
from p2p_recon.agents.providers import OfflineProvider
from p2p_recon.agents.tools import LineKey
from p2p_recon.agents.workflow import AgentRun, agent_connection
from p2p_recon.db import connect_app, connect_as

pytestmark = pytest.mark.integration

ADMIN = "admin_reviewer"
HELD_PRICE = LineKey("SAP_LIKE", "5100000002", 1)  # S03, GB01
HELD_DE = LineKey("ORACLE_LIKE", "90002", 1)  # DE01
# Protected tables the reviewer is deliberately not granted, with the reason.
NOT_GRANTED = {"core.supplier_bank_account": "bank numbers: the masked view only"}
# Tables that must hold rows here, so that "the admin sees all of them" cannot pass on two empty tables.
MUST_HAVE_ROWS = (
    "core.supplier_invoice",
    "core.invoice_line",
    "recon.match_result",
    "recon.exception",
    "mdm.supplier_xref",
    "docs.chunk",
    "agent.proposal",
    "agent.step",
    "agent.tool_call",
    "agent.citation",
)


def count(conn, table):
    return conn.execute(f"select count(*) as n from {table}").fetchone()["n"]


@pytest.fixture(scope="module")
def trail(graph_synced, loaded_documents):
    """Proposals in both companies, made for the people who may see them."""
    with graph.driver() as db, agent_connection() as conn:
        ids = {}
        for user, key in (("ap_analyst_uk", HELD_PRICE), ("ap_analyst_de", HELD_DE)):
            run = AgentRun(conn, db, OfflineProvider(), user)
            ids[user] = run.investigate(key).proposal_id
            run.finish()
    return ids


# ---------------------------------------------------------------- what the reviewer is not


def test_reviewer_is_not_a_person_agents_can_act_for(pipeline, conn):
    """Both foreign keys point at agent.acting_user, so this holds whatever a grant or policy says."""
    run_id = conn.execute("select max(run_id) as id from recon.match_run").fetchone()["id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "insert into agent.run (provider, model, acting_user, recon_run_id, status) values ('offline', 'x', %s, %s, 'running')",
            (ADMIN, run_id),
        )


def test_reviewer_cannot_be_named_on_a_decision(trail, conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "insert into agent.human_decision (proposal_id, decision, decided_by) values (%s, 'ACCEPT', %s)",
            (trail["ap_analyst_uk"], ADMIN),
        )


def test_reviewer_login_is_assumed_not_inherited(pipeline):
    with connect_app() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select count(*) from agent.admin_review_note")


# ---------------------------------------------------------------- reads


def protected_tables(conn) -> list[str]:
    return [
        r["name"]
        for r in conn.execute(
            """
            select c.relnamespace::regnamespace::text || '.' || c.relname as name
            from pg_class c
            where c.relkind = 'r' and c.relrowsecurity
              and c.relnamespace::regnamespace::text in ('core', 'recon', 'docs', 'agent', 'mdm', 'ops')
            order by 1
            """
        )
    ]


def test_reviewer_sees_every_row_of_every_protected_table(trail, conn):
    """Read from the catalogue, so a table protected later is checked without anyone adding it here."""
    tables = protected_tables(conn)
    assert set(MUST_HAVE_ROWS) <= set(tables)
    with connect_as(ADMIN) as admin:
        for table in tables:
            if table in NOT_GRANTED:
                continue
            assert count(admin, table) == count(conn, table), table
    for table in MUST_HAVE_ROWS:
        assert count(conn, table) > 0, table


def test_reviewer_sees_both_companies(trail):
    with connect_as(ADMIN) as admin:
        for table in ("core.supplier_invoice", "recon.current_match_result", "agent.proposal", "docs.chunk"):
            companies = {
                r["company_code"] for r in admin.execute(f"select distinct company_code from {table}")
            }
            assert {"GB01", "DE01"} <= companies, table


def test_reviewer_reads_audit_memos(pipeline, loaded_documents):
    with connect_as(ADMIN) as admin:
        assert count(admin, "docs.document where doc_type = 'audit_memo'") > 0
        assert count(admin, "docs.chunk where document_id like 'audit_memo%'") > 0


@pytest.mark.parametrize(
    "table",
    [
        "ops.pipeline_run",
        "ops.dq_result",
        "ops.lineage_edge",
        "ops.quarantine",
        "ops.watermark",
        "ops.ingest_file",
        "mdm.supplier_golden",
        "mdm.supplier_match_candidate",
        "mdm.steward_decision",
        "agent.audit_log",
        "agent.review_queue",
        "core.policy_parameter",
    ],
)
def test_reviewer_reads_oversight_tables(trail, conn, table):
    with connect_as(ADMIN) as admin:
        assert count(admin, table) == count(conn, table)


def test_reviewer_reads_masked_bank_numbers_of_both_companies(pipeline, conn):
    real = {
        r["account_number"] for r in conn.execute("select account_number from core.supplier_bank_account")
    }
    with connect_as(ADMIN) as admin:
        rows = admin.execute(
            "select company_code, account_number_masked from core.v_supplier_bank_account_masked"
        ).fetchall()
    assert {r["company_code"] for r in rows} == {"GB01", "DE01"}
    assert all(r["account_number_masked"] not in real for r in rows)


@pytest.mark.parametrize(
    "statement",
    [
        "select account_number from core.supplier_bank_account limit 1",
        "select * from raw.sap_supplier_bank limit 1",
        "select * from raw.ora_invoices limit 1",
    ],
)
def test_reviewer_cannot_read_bank_numbers_or_raw(pipeline, statement):
    with connect_as(ADMIN) as admin, pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute(statement)


# ---------------------------------------------------------------- writes


@pytest.mark.parametrize(
    "statement",
    [
        "update core.supplier_invoice set net_amount = 0",
        "delete from core.invoice_line",
        "update core.policy_parameter set parameter_value = 0",
        "update recon.match_result set status_code = 'Matched'",
        "delete from recon.exception",
        "update mdm.supplier_xref set supplier_gid = 'x'",
        "insert into mdm.steward_decision (a_source_system, a_source_supplier_id, b_source_system, b_source_supplier_id, decision, decided_by) values ('a', 'b', 'c', 'd', 'MERGE', 'admin_reviewer')",
        "update docs.chunk set content = ''",
        "update agent.proposal set proposed_action = 'AUTO_APPROVE'",
        "insert into agent.human_decision (proposal_id, decision, decided_by) values (1, 'ACCEPT', 'admin_reviewer')",
        "insert into agent.audit_log (event, session_user_name, role_in_use, detail) values ('admin_review_note', 'a', 'b', '{}')",
        "insert into ops.admin_view_log (session_user_name, role_in_use, page) values ('a', 'b', 'c')",
        "update agent.reviewer set company_codes = '{}'",
        "insert into agent.acting_user values ('admin_reviewer', 'x', '{GB01}', '{ap}')",
        "update agent.admin_review_note set note = 'tidied'",
        "delete from agent.admin_review_note",
    ],
)
def test_reviewer_cannot_change_the_books_the_trail_or_its_own_scope(pipeline, statement):
    with connect_as(ADMIN) as admin, pytest.raises(psycopg.errors.InsufficientPrivilege):
        admin.execute(statement)


def test_reviewer_cannot_decide(trail):
    with connect_as(ADMIN) as admin:
        assert review.proposal_detail(admin, trail["ap_analyst_uk"]) is not None
        with pytest.raises(review.DecisionRefused):
            review.decide(admin, trail["ap_analyst_uk"], "ACCEPT")


def test_reviewer_writes_a_note_and_a_follow_up(trail, conn):
    proposal = str(trail["ap_analyst_de"])
    with connect_as(ADMIN) as admin:
        first = notes.add_note(
            admin, "proposal", proposal, "citation", "Cited clause is the superseded version", follow_up=True
        )
        assert [n["open"] for n in notes.notes(admin, "proposal", proposal)] == [True]
        second = notes.add_note(
            admin, "proposal", proposal, "citation", "Corpus reloaded; citation now current", follows_up=first
        )
        rows = notes.notes(admin, "proposal", proposal)
    assert [(n["note_id"], n["written_by"], n["open"]) for n in rows] == [
        (second, ADMIN, False),
        (first, ADMIN, False),
    ]
    conn.rollback()
    audited = conn.execute(
        "select role_in_use, on_behalf_of, session_user_name, proposal_id, detail from agent.audit_log "
        "where event = 'admin_review_note' and (detail->>'note_id')::bigint in (%s, %s) order by audit_id",
        (first, second),
    ).fetchall()
    assert [(a["role_in_use"], a["on_behalf_of"], a["session_user_name"]) for a in audited] == [
        (ADMIN, ADMIN, "p2p_app")
    ] * 2
    assert audited[0]["proposal_id"] == trail["ap_analyst_de"]
    assert audited[1]["detail"]["follows_up"] == first


def test_note_cannot_be_written_in_another_name_or_backdated(pipeline):
    with connect_as(ADMIN) as admin:
        for column, value in (("written_by", "controller"), ("written_at", "2020-01-01")):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                admin.execute(
                    f"insert into agent.admin_review_note (object_type, object_id, category, note, {column}) "
                    "values ('rule', 'r', 'other', 'x', %s)",
                    (value,),
                )


def test_note_needs_text_and_a_known_category(pipeline):
    with connect_as(ADMIN) as admin:
        with pytest.raises(notes.NoteRefused):
            notes.add_note(admin, "rule", "PriceVariance", "other", "   ")
        with pytest.raises(notes.NoteRefused):
            notes.add_note(admin, "rule", "PriceVariance", "opinion", "x")
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute(
                "insert into agent.admin_review_note (object_type, object_id, category, note) values ('rule', 'r', 'opinion', 'x')"
            )


@pytest.mark.parametrize("role", ["ap_analyst_uk", "controller", "auditor"])
def test_only_the_reviewer_writes_notes(pipeline, role):
    with connect_as(role) as conn, pytest.raises(notes.NoteRefused):
        notes.add_note(conn, "rule", "PriceVariance", "other", "Not mine to write")


@pytest.mark.parametrize(
    ("constraint", "values"),
    [
        ("admin_review_note_object_type_check", notes.OBJECT_TYPES),
        ("admin_review_note_category_check", notes.CATEGORIES),
    ],
)
def test_note_vocabularies_match_the_database(pipeline, conn, constraint, values):
    definition = conn.execute(
        "select pg_get_constraintdef(oid) as d from pg_constraint where conname = %s", (constraint,)
    ).fetchone()["d"]
    assert set(re.findall(r"'([a-z_]+)'::text", definition)) == set(values)


# ---------------------------------------------------------------- the view log


def test_view_log_records_the_role_and_the_object(pipeline, conn):
    with connect_as(ADMIN) as admin:
        view_id = notes.log_view(admin, "agents", "agent_run", 7)
        assert count(admin, f"ops.admin_view_log where view_id = {view_id}") == 1
    conn.rollback()
    row = conn.execute("select * from ops.admin_view_log where view_id = %s", (view_id,)).fetchone()
    assert (
        row["session_user_name"],
        row["role_in_use"],
        row["page"],
        row["object_type"],
        row["object_id"],
    ) == (
        "p2p_app",
        ADMIN,
        "agents",
        "agent_run",
        "7",
    )


@pytest.mark.parametrize("role", ["ap_analyst_uk", "controller", "auditor", "agent_service"])
def test_only_the_reviewer_can_call_the_view_log(pipeline, conn, role):
    """The EXECUTE grant is checked on its own, because the function's role check would refuse the call anyway."""
    granted = conn.execute(
        "select has_function_privilege(%s, 'ops.log_admin_view(text, text, text)', 'execute') as ok", (role,)
    ).fetchone()["ok"]
    assert granted is False
    with connect_as(role) as person, pytest.raises(psycopg.errors.InsufficientPrivilege):
        notes.log_view(person, "overview")


def test_view_log_refuses_a_caller_who_is_not_the_reviewer(pipeline, conn):
    """The function checks the role itself, in case an EXECUTE grant is ever widened by mistake."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="only admin_reviewer"):
        conn.execute("select ops.log_admin_view('overview')")


@pytest.mark.parametrize(
    "statement",
    [
        "update agent.admin_review_note set note = 'tidied'",
        "delete from agent.admin_review_note",
        "truncate agent.admin_review_note",
        "update ops.admin_view_log set page = 'nothing'",
        "delete from ops.admin_view_log",
        "truncate ops.admin_view_log",
    ],
)
def test_notes_and_view_log_cannot_be_rewritten_even_by_the_owner(pipeline, conn, statement):
    with connect_as(ADMIN) as admin:
        notes.add_note(admin, "rule", "DuplicateInvoice", "control_design", "Owner test seed")
        notes.log_view(admin, "overview")
    conn.rollback()
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute(statement)
    conn.rollback()


# ---------------------------------------------------------------- who reads the reviewer's own record


@pytest.mark.parametrize(
    ("role", "reads_notes", "reads_view_log"),
    [
        ("ap_analyst_uk", False, False),
        ("ap_analyst_de", False, False),
        ("controller", True, False),
        ("auditor", True, True),
        ("agent_service", False, False),
    ],
)
def test_who_reads_notes_and_the_view_log(pipeline, conn, role, reads_notes, reads_view_log):
    # The table grant itself, so that a missing schema grant cannot hide a wrong table grant.
    for table, allowed in (("agent.admin_review_note", reads_notes), ("ops.admin_view_log", reads_view_log)):
        granted = conn.execute(
            "select has_table_privilege(%s, %s, 'select') as ok", (role, table)
        ).fetchone()["ok"]
        assert granted is allowed, table
    with connect_as(role) as person:
        for table, allowed in (
            ("agent.admin_review_note", reads_notes),
            ("ops.admin_view_log", reads_view_log),
        ):
            if allowed:
                assert count(person, table) >= 0
            else:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    count(person, table)


@pytest.mark.parametrize(("role", "company"), [("ap_analyst_uk", "GB01"), ("ap_analyst_de", "DE01")])
def test_analysts_scope_is_unchanged_by_the_reviewer(trail, role, company):
    """visible_companies() now also reads agent.reviewer; an analyst must still get exactly one company."""
    with connect_as(role) as conn:
        assert conn.execute("select agent.visible_companies() as c").fetchone()["c"] == [company]
        assert {
            r["company_code"] for r in conn.execute("select distinct company_code from agent.proposal")
        } == {company}
