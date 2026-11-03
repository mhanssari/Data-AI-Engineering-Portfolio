"""Contracts are complete, the database honours them, and a change that would hurt a consumer fails without a major version."""

import copy
from datetime import datetime, timedelta

import pytest

from p2p_recon import contracts
from p2p_recon.generate import BATCH_2_EXTRACTED

pytestmark = pytest.mark.integration

ALL = contracts.load_contracts()
EXPECTED_PRODUCTS = {"supplier", "purchase_order", "invoice", "match_result"}
LATEST_EXTRACT = datetime.fromisoformat(BATCH_2_EXTRACTED)


@pytest.fixture
def invoice():
    return copy.deepcopy(ALL["invoice"])


def test_every_data_product_has_a_contract():
    assert set(ALL) == EXPECTED_PRODUCTS


@pytest.mark.parametrize("name", sorted(EXPECTED_PRODUCTS))
def test_contract_is_complete(name):
    assert contracts.validate_shape(ALL[name]) == []


@pytest.mark.parametrize("name", sorted(EXPECTED_PRODUCTS))
def test_database_honours_the_contract(pipeline, conn, name):
    assert contracts.validate_data(conn, ALL[name]) == []


@pytest.mark.parametrize("name", sorted(EXPECTED_PRODUCTS))
def test_repository_has_no_unversioned_breaking_change(name):
    released = contracts.load_contract(contracts.RELEASED_DIR / f"{name}.yaml")
    assert contracts.check_release(released, ALL[name]) == []


def test_check_all_is_clean(pipeline, conn):
    results = contracts.check_all(conn, LATEST_EXTRACT + timedelta(hours=1))
    assert {name: r["problems"] for name, r in results.items() if r["problems"]} == {}


# ---------------------------------------------------------------- the checks can fail


def test_shape_finds_what_is_missing(invoice):
    del invoice["owner"]
    invoice["version"] = "one"
    invoice["tables"]["core.invoice_line"]["key"] = ["no_such_column"]
    problems = contracts.validate_shape(invoice)
    assert "missing owner" in problems
    assert any("MAJOR.MINOR.PATCH" in p for p in problems)
    assert any("no_such_column" in p for p in problems)


@pytest.mark.parametrize(
    ("ddl", "expected"),
    [
        (
            "alter table core.invoice_line drop column net_amount cascade",
            "core.invoice_line.net_amount: declared but missing",
        ),
        (
            "alter table core.invoice_line add column surprise text",
            "core.invoice_line.surprise: exists but is not declared",
        ),
        (
            "alter table core.invoice_line alter column net_amount type text using net_amount::text",
            "core.invoice_line.net_amount: type is text",
        ),
        (
            "alter table core.invoice_line drop constraint invoice_line_pkey cascade",
            "core.invoice_line: primary key",
        ),
        (
            "alter table core.supplier_invoice disable row level security",
            "core.supplier_invoice: row-level security is promised and is off",
        ),
        (
            "grant select on core.invoice_line to agent_service",
            "core.invoice_line: agent_service should not be able to read it",
        ),
        (
            "revoke select on core.invoice_line from auditor",
            "core.invoice_line: auditor should be able to read it",
        ),
    ],
)
def test_live_drift_is_detected(pipeline, conn, invoice, ddl, expected):
    conn.rollback()
    conn.execute(ddl)
    try:
        assert any(p.startswith(expected) for p in contracts.validate_data(conn, invoice)), (
            contracts.validate_data(conn, invoice)
        )
    finally:
        conn.rollback()


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"check": "min_rows", "table": "core.invoice_line", "min": 10_000_000}, "rows, contract says"),
        (
            {
                "check": "accepted_values",
                "table": "core.supplier_invoice",
                "column": "document_type",
                "values": ["INV"],
            },
            "unexpected ['CRN']",
        ),
        ({"check": "non_negative", "table": "core.invoice_line", "column": "net_amount"}, "negative values"),
    ],
)
def test_quality_breach_is_detected(pipeline, conn, invoice, change, expected):
    invoice["quality"].append(change)
    assert any(expected in p for p in contracts.validate_data(conn, invoice))


def test_null_rate_breach_is_detected(pipeline, conn, invoice):
    conn.rollback()
    conn.execute("alter table core.invoice_line alter column net_amount drop not null")
    conn.execute(
        "update core.invoice_line set net_amount = null where (source_system, invoice_id, line_number) in (select source_system, invoice_id, line_number from core.invoice_line limit 3)"
    )
    try:
        assert any("null rate" in p for p in contracts.validate_data(conn, invoice))
    finally:
        conn.rollback()


# ---------------------------------------------------------------- freshness, on an injected clock


def test_freshness_inside_and_outside_the_sla(pipeline, conn, invoice):
    sla = timedelta(hours=invoice["freshness"]["sla_hours"])
    assert contracts.check_freshness(conn, invoice, LATEST_EXTRACT + sla - timedelta(minutes=1)) == []
    assert (
        "SLA is" in contracts.check_freshness(conn, invoice, LATEST_EXTRACT + sla + timedelta(minutes=1))[0]
    )


def test_freshness_with_no_data_is_a_problem(pipeline, conn, invoice):
    invoice["freshness"]["source_systems"] = ["NOT_DELIVERED"]
    assert "nothing ingested" in contracts.check_freshness(conn, invoice, LATEST_EXTRACT)[0]


# ---------------------------------------------------------------- breaking changes


def _remove_column(c):
    del c["tables"]["core.invoice_line"]["columns"]["net_amount"]


def _retype(c):
    c["tables"]["core.invoice_line"]["columns"]["net_amount"]["type"] = "text"


def _nullable(c):
    c["tables"]["core.invoice_line"]["columns"]["net_amount"]["nullable"] = True


def _rekey(c):
    c["tables"]["core.invoice_line"]["key"] = ["invoice_id", "line_number"]


def _regrain(c):
    c["tables"]["core.invoice_line"]["grain"] = "One invoice"


def _remove_table(c):
    del c["tables"]["core.invoice_line"]


def _weaken_min(c):
    next(q for q in c["quality"] if q["check"] == "min_rows")["min"] = 1


def _weaken_max(c):
    next(q for q in c["quality"] if q["check"] == "max_null_rate")["max"] = 0.5


def _new_value(c):
    next(q for q in c["quality"] if q["check"] == "accepted_values")["values"].append("DEBIT")


def _drop_check(c):
    c["quality"].pop()


def _slower(c):
    c["freshness"]["sla_hours"] = 96


BREAKING = [
    _remove_column,
    _retype,
    _nullable,
    _rekey,
    _regrain,
    _remove_table,
    _weaken_min,
    _weaken_max,
    _new_value,
    _drop_check,
    _slower,
]


@pytest.mark.parametrize("change", BREAKING, ids=lambda f: f.__name__)
def test_breaking_change_fails_without_a_major_version(invoice, change):
    released = copy.deepcopy(invoice)
    change(invoice)
    assert contracts.breaking_changes(released, invoice)
    invoice["version"] = "1.1.0"
    assert any(
        "breaking without a major version bump" in p for p in contracts.check_release(released, invoice)
    )


@pytest.mark.parametrize("change", BREAKING, ids=lambda f: f.__name__)
def test_breaking_change_passes_with_a_major_version(invoice, change):
    released = copy.deepcopy(invoice)
    change(invoice)
    invoice["version"] = "2.0.0"
    assert contracts.check_release(released, invoice) == []


def test_addition_and_tightening_are_not_breaking(invoice):
    released = copy.deepcopy(invoice)
    invoice["tables"]["core.invoice_line"]["columns"]["new_column"] = {"type": "text", "nullable": True}
    next(q for q in invoice["quality"] if q["check"] == "min_rows")["min"] += 1
    invoice["freshness"]["sla_hours"] = 24
    assert contracts.breaking_changes(released, invoice) == []
    assert "still at version" in contracts.check_release(released, invoice)[0]
    invoice["version"] = "1.1.0"
    assert contracts.check_release(released, invoice) == []
