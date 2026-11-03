"""The database enforces who sees and does what. Every test here connects through the application login and a real role.

The pipeline's own login is a superuser and bypasses row-level security, so a test on the `conn` fixture would pass with
no policies at all. These tests use it only to set up data and to know the true totals.
"""

import psycopg
import pytest

from p2p_recon import graph, retrieval, review
from p2p_recon.agents.providers import OfflineProvider
from p2p_recon.agents.tools import LineKey, ToolBox
from p2p_recon.agents.workflow import AgentRun, agent_connection
from p2p_recon.db import assume_role, connect_app, connect_as

pytestmark = pytest.mark.integration

ANALYSTS = {"ap_analyst_uk": "GB01", "ap_analyst_de": "DE01"}
PEOPLE = ("ap_analyst_uk", "ap_analyst_de", "controller", "auditor")
COMPANY_TABLES = (
    "core.supplier",
    "core.employee",
    "core.cost_centre",
    "core.gl_account",
    "core.contract",
    "core.purchase_requisition",
    "core.purchase_order",
    "core.po_line",
    "core.goods_receipt",
    "core.supplier_invoice",
    "recon.control_breach",
    "recon.match_result",
    "recon.grni_line",
    "recon.accrual_journal_line",
    "recon.current_match_result",
    "recon.current_control_breach",
    "recon.current_grni_line",
    "recon.current_accrual_journal_line",
    "recon.current_payment_readiness",
    "docs.document",
    "docs.chunk",
)
# Tables with no company_code of their own: the policy goes through the parent, shown here by a join back to it.
PARENT_TABLES = {
    "core.invoice_line": "core.supplier_invoice p on p.source_system = t.source_system and p.invoice_id = t.invoice_id",
    "core.grn_line": "core.goods_receipt p on p.source_system = t.source_system and p.grn_number = t.grn_number",
    "core.approval": "core.purchase_requisition p on p.source_system = t.source_system and p.pr_number = t.pr_number",
    "recon.exception": "recon.match_result p on p.run_id = t.run_id and p.source_system = t.source_system and p.invoice_id = t.invoice_id and p.line_number = t.line_number",
    "recon.current_exception": "recon.match_result p on p.run_id = t.run_id and p.source_system = t.source_system and p.invoice_id = t.invoice_id and p.line_number = t.line_number",
}
HELD_PRICE = LineKey("SAP_LIKE", "5100000002", 1)  # S03, GB01, no control reason
HELD_CONTROL = LineKey("SAP_LIKE", "5100000007", 1)  # S11, GB01, ApprovalLimitExceeded


@pytest.fixture(scope="module")
def db(graph_synced):
    with graph.driver() as driver:
        yield driver


def count(conn, table):
    return conn.execute(f"select count(*) as n from {table}").fetchone()["n"]


# ---------------------------------------------------------------- the login itself


def test_application_login_is_not_privileged(pipeline):
    with connect_app() as conn:
        row = conn.execute(
            "select rolsuper, rolbypassrls, rolinherit, current_user as who from pg_roles where rolname = current_user"
        ).fetchone()
        assert (row["rolsuper"], row["rolbypassrls"], row["rolinherit"], row["who"]) == (
            False,
            False,
            False,
            "p2p_app",
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select * from core.supplier_invoice limit 1")


def test_login_cannot_assume_the_pipeline_role(pipeline, conn):
    admin = conn.execute("select current_user as who").fetchone()["who"]
    with connect_app() as app, pytest.raises(psycopg.errors.InsufficientPrivilege):
        assume_role(app, admin)


def test_row_security_cannot_be_switched_off(pipeline):
    with connect_as("ap_analyst_uk") as conn:
        conn.execute("set row_security = off")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select count(*) from core.supplier_invoice").fetchone()


# ---------------------------------------------------------------- catalogue checks: nothing added later can slip past


def test_every_table_with_a_company_has_row_security(pipeline, conn):
    rows = conn.execute(
        """
        select c.relnamespace::regnamespace::text || '.' || c.relname as name
        from pg_class c join pg_attribute a on a.attrelid = c.oid and a.attname = 'company_code' and not a.attisdropped
        where c.relkind = 'r' and c.relnamespace::regnamespace::text in ('core', 'recon', 'docs', 'agent')
          and not c.relrowsecurity and c.relname <> 'legal_entity'
        """
    ).fetchall()
    assert rows == []


def test_every_view_runs_as_its_reader(pipeline, conn):
    """A view runs as its owner by default, which would walk straight past row-level security."""
    rows = conn.execute(
        """
        select c.relnamespace::regnamespace::text || '.' || c.relname as name
        from pg_class c
        where c.relkind = 'v' and c.relnamespace::regnamespace::text in ('core', 'recon', 'docs', 'agent', 'mdm')
          and not coalesce('security_invoker=true' = any(c.reloptions), false)
        """
    ).fetchall()
    # The one exception reads a column no role may read, and filters by company itself.
    assert [r["name"] for r in rows] == ["core.v_supplier_bank_account_masked"]


# ---------------------------------------------------------------- allowed and denied rows


@pytest.mark.parametrize("table", COMPANY_TABLES)
@pytest.mark.parametrize(("role", "company"), ANALYSTS.items())
def test_analyst_sees_only_their_company(pipeline, loaded_documents, role, company, table):
    with connect_as(role) as conn:
        companies = {r["company_code"] for r in conn.execute(f"select distinct company_code from {table}")}
    assert companies <= {company, "GROUP"}
    assert company in companies or table.endswith(("grni_line", "accrual_journal_line", "control_breach"))


@pytest.mark.parametrize("table", list(PARENT_TABLES))
@pytest.mark.parametrize(("role", "company"), ANALYSTS.items())
def test_rows_with_no_company_of_their_own_follow_their_parent(pipeline, conn, role, company, table):
    with connect_as(role) as analyst:
        visible = count(analyst, table)
    expected = conn.execute(
        f"select count(*) as n from {table} t join {PARENT_TABLES[table]} where p.company_code = %s",
        (company,),
    ).fetchone()["n"]
    assert visible == expected > 0


@pytest.mark.parametrize(
    "table",
    ["core.supplier_invoice", "recon.current_match_result", "core.invoice_line", "recon.current_exception"],
)
@pytest.mark.parametrize("role", ["controller", "auditor"])
def test_group_roles_see_everything(pipeline, conn, role, table):
    with connect_as(role) as group_role:
        assert count(group_role, table) == count(conn, table) > 0


def test_analysts_partition_the_data(pipeline, conn):
    total = count(conn, "recon.current_match_result")
    seen = []
    for role in ANALYSTS:
        with connect_as(role) as analyst:
            seen.append(count(analyst, "recon.current_match_result"))
    assert sum(seen) == total
    assert all(seen)


def test_crosswalk_does_not_name_the_other_erps_suppliers(pipeline):
    with connect_as("ap_analyst_de") as conn:
        assert {
            r["source_system"] for r in conn.execute("select distinct source_system from mdm.supplier_xref")
        } == {"ORACLE_LIKE"}
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select * from mdm.supplier_match_candidate limit 1")


@pytest.mark.parametrize(
    ("role", "allowed"),
    [("ap_analyst_uk", False), ("ap_analyst_de", False), ("controller", False), ("auditor", True)],
)
def test_audit_memos_need_the_audit_tag(pipeline, loaded_documents, role, allowed):
    with connect_as(role) as conn:
        assert (count(conn, "docs.chunk where document_id like 'audit_memo%'") > 0) is allowed
        assert (count(conn, "docs.document where doc_type = 'audit_memo'") > 0) is allowed


@pytest.mark.parametrize("method", ["keyword", "vector"])
def test_row_security_holds_when_the_application_filter_is_gone(
    pipeline, loaded_documents, monkeypatch, method
):
    """The two layers are independent: with the WHERE clause removed from retrieval, the database still refuses."""
    monkeypatch.setattr(retrieval, "PERMITTED", "true")
    with connect_as("ap_analyst_uk") as conn:
        user = retrieval.load_user(conn, "ap_analyst_uk")
        ids = retrieval.ranked_ids(conn, user, "What is the price tolerance for Jena Textil GmbH?", method)
        assert ids
        companies = {
            r["company_code"]
            for r in conn.execute(
                "select distinct company_code from docs.chunk where chunk_id = any(%s)", (ids,)
            )
        }
        assert companies <= {"GB01", "GROUP"}


# ---------------------------------------------------------------- bank numbers


@pytest.mark.parametrize("role", PEOPLE + ("agent_service",))
def test_nobody_reads_a_bank_number(pipeline, role):
    with connect_app() as conn:
        assume_role(conn, role)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select account_number from core.supplier_bank_account limit 1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select * from raw.sap_supplier_bank limit 1")


@pytest.mark.parametrize(("role", "company"), [*ANALYSTS.items(), ("controller", None), ("auditor", None)])
def test_masked_view_shows_only_the_last_four_of_permitted_companies(pipeline, conn, role, company):
    real = {
        r["account_number"] for r in conn.execute("select account_number from core.supplier_bank_account")
    }
    with connect_as(role) as person:
        rows = person.execute(
            "select company_code, account_number_masked from core.v_supplier_bank_account_masked"
        ).fetchall()
    assert rows
    assert all(r["account_number_masked"] not in real for r in rows)
    assert all(set(r["account_number_masked"][:-4]) == {"*"} for r in rows)
    assert {r["account_number_masked"][-4:] for r in rows} <= {number[-4:] for number in real}
    if company:
        assert {r["company_code"] for r in rows} == {company}


# ---------------------------------------------------------------- writes


@pytest.mark.parametrize("role", PEOPLE)
@pytest.mark.parametrize(
    "statement",
    [
        "update core.supplier_invoice set net_amount = 0",
        "delete from core.invoice_line",
        "update recon.match_result set status_code = 'Matched'",
        "delete from recon.exception",
        "update docs.chunk set content = ''",
        "insert into agent.proposal (agent_run_id, source_system, invoice_id, line_number, company_code, sql_status, sql_reasons, proposed_action, explanation) values (1, 'a', 'b', 1, 'GB01', 'x', '{}', 'AUTO_APPROVE', 'x')",
        "update agent.proposal set proposed_action = 'AUTO_APPROVE'",
        "insert into agent.audit_log (event, session_user_name, role_in_use, detail) values ('human_decision', 'a', 'b', '{}')",
    ],
)
def test_people_cannot_change_the_books_or_the_trail(pipeline, role, statement):
    with connect_as(role) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement)


# ---------------------------------------------------------------- on behalf of a person


def test_agent_service_itself_reads_no_business_data(graph_synced):
    with agent_connection() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from recon.match_result limit 1")


def test_tools_run_as_the_person_and_revert(graph_synced, db):
    with agent_connection() as conn:
        user = retrieval.load_user(conn, "ap_analyst_de")
        tools = ToolBox(conn, db, user)
        run = AgentRun(conn, db, OfflineProvider(), "ap_analyst_de", {"evaluation": "test"})
        tools.step_id = run._begin_step(LineKey("ORACLE_LIKE", "90002", 1), "test")
        inside = tools._call(
            "probe", {}, lambda: conn.execute("select current_user as who").fetchone()["who"]
        )
        assert inside == "ap_analyst_de"
        assert conn.execute("select current_user as who").fetchone()["who"] == "agent_service"


def test_agents_inherit_the_persons_limits_with_the_query_filter_gone(graph_synced, db):
    """Removing the company filter from a tool's query changes nothing: the acting role's policies still apply."""
    with agent_connection() as conn:
        user = retrieval.load_user(conn, "ap_analyst_de")
        tools = ToolBox(conn, db, user)
        run = AgentRun(conn, db, OfflineProvider(), "ap_analyst_de", {"evaluation": "test"})
        tools.step_id = run._begin_step(LineKey("ORACLE_LIKE", "90002", 1), "test")
        count_all = tools._call(
            "probe",
            {},
            lambda: conn.execute(
                "select count(*) as n, count(*) filter (where company_code = 'GB01') as uk from recon.current_match_result"
            ).fetchone(),
        )
        assert count_all["n"] > 0
        assert count_all["uk"] == 0


# ---------------------------------------------------------------- decisions, enforced by the policy


@pytest.fixture
def held_proposals(graph_synced, db):
    with agent_connection() as conn:
        run = AgentRun(conn, db, OfflineProvider(), "controller")
        ids = {
            "price": run.investigate(HELD_PRICE).proposal_id,
            "control": run.investigate(HELD_CONTROL).proposal_id,
        }
        run.finish()
    return ids


def test_analyst_may_decide_an_ordinary_exception_in_their_company(held_proposals):
    with connect_as("ap_analyst_uk") as conn:
        assert review.decide(conn, held_proposals["price"], "ACCEPT") > 0


def test_analyst_may_not_clear_a_failed_control(held_proposals):
    with connect_as("ap_analyst_uk") as conn, pytest.raises(review.DecisionRefused):
        review.decide(conn, held_proposals["control"], "ACCEPT")


def test_controller_may_clear_a_failed_control(held_proposals):
    with connect_as("controller") as conn:
        assert (
            review.decide(
                conn, held_proposals["control"], "OVERRIDE", "Limit confirmed with the finance director"
            )
            > 0
        )


def test_other_companys_analyst_may_not_decide(held_proposals):
    with connect_as("ap_analyst_de") as conn:
        assert review.proposal_detail(conn, held_proposals["price"]) is None
        with pytest.raises(review.DecisionRefused):
            review.decide(conn, held_proposals["price"], "ACCEPT")


def test_auditor_is_read_only(held_proposals):
    with connect_as("auditor") as conn:
        assert review.proposal_detail(conn, held_proposals["price"]) is not None
        with pytest.raises(review.DecisionRefused):
            review.decide(conn, held_proposals["price"], "ACCEPT")


def test_nobody_can_decide_in_someone_elses_name(held_proposals):
    with connect_as("ap_analyst_uk") as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            "insert into agent.human_decision (proposal_id, decision, decided_by) values (%s, 'ACCEPT', 'controller')",
            (held_proposals["price"],),
        )


def test_agent_cannot_decide(held_proposals):
    with agent_connection() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            "insert into agent.human_decision (proposal_id, decision, decided_by) values (%s, 'ACCEPT', 'controller')",
            (held_proposals["price"],),
        )


def test_override_needs_a_reason(held_proposals):
    with connect_as("ap_analyst_uk") as conn, pytest.raises(review.DecisionRefused, match="say why"):
        review.decide(conn, held_proposals["price"], "OVERRIDE", "  ")


# ---------------------------------------------------------------- audit log


def test_actions_reach_the_audit_log_with_who_acted_for_whom(held_proposals, conn):
    with connect_as("ap_analyst_uk") as analyst:
        review.decide(analyst, held_proposals["price"], "QUERY", "Asked the buyer")
    conn.rollback()
    rows = conn.execute(
        "select event, role_in_use, on_behalf_of, session_user_name, detail from agent.audit_log where proposal_id = %s order by audit_id",
        (held_proposals["price"],),
    ).fetchall()
    assert [r["event"] for r in rows] == ["proposal_stored", "human_decision"]
    stored, decided = rows
    assert (stored["role_in_use"], stored["on_behalf_of"], stored["session_user_name"]) == (
        "agent_service",
        "controller",
        "p2p_app",
    )
    assert (decided["role_in_use"], decided["on_behalf_of"]) == ("ap_analyst_uk", "ap_analyst_uk")
    assert decided["detail"] == {"decision": "QUERY", "note": "Asked the buyer"}


@pytest.mark.parametrize(
    ("role", "allowed"),
    [("ap_analyst_uk", False), ("ap_analyst_de", False), ("controller", True), ("auditor", True)],
)
def test_audit_log_is_for_oversight_roles(pipeline, graph_synced, role, allowed):
    with connect_as(role) as conn:
        if allowed:
            assert count(conn, "agent.audit_log") >= 0
        else:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                count(conn, "agent.audit_log")


@pytest.mark.parametrize(
    "statement",
    [
        "update agent.audit_log set role_in_use = 'nobody'",
        "delete from agent.audit_log",
        "truncate agent.audit_log",
        "truncate agent.proposal cascade",
        "truncate agent.citation",
        "truncate agent.human_decision",
        "delete from agent.human_decision",
        "delete from agent.citation",
    ],
)
def test_the_trail_cannot_be_rewritten_even_by_the_owner(held_proposals, conn, statement):
    conn.rollback()
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute(statement)
    conn.rollback()
