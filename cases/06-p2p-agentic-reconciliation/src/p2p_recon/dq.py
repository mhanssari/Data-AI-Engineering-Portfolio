"""Data-quality checks and finance reconciliations, recorded per run in ops.dq_result.

The control totals are computed from raw with their own parsing rather than through stg, so a bug in a staging view
shows up as a difference instead of being reproduced on both sides. Open quarantine is the one permitted reconciling
item, and the detail column states it, because a total that only balances after a silent exclusion is no control.
"""

from dataclasses import dataclass
from decimal import Decimal

import psycopg

from p2p_recon.conform import ENTITIES
from p2p_recon.db import connect, pipeline_run
from p2p_recon.ontology import concept_codes

FRESHNESS_LAG_HOURS = 48

# Each query returns one row per scope: scope, raw_total, quarantined_total. The latest version of each document wins,
# CDC deletes are excluded and credit-note signs are normalised, independently of stg.
CONTROL_TOTALS = {
    "invoice_net_amount": (
        "supplier_invoice",
        "core.supplier_invoice",
        "net_amount",
        """
        with latest as (
            select distinct on (belnr) 'SAP_LIKE|' || belnr as natural_key, bukrs as company, waers as currency,
                   case when blart = 'KG' then -netwr::numeric else netwr::numeric end as value, cdc_op
            from raw.sap_invoice_header order by belnr, changed_at desc, raw_id desc
        ), latest_ora as (
            select distinct on (invoice_id) 'ORACLE_LIKE|' || invoice_id as natural_key, m.company_code as company,
                   invoice_currency_code as currency, invoice_amount::numeric as value, cdc_op
            from raw.ora_invoices r left join stg.org_map m on m.source_org_ref = r.org_id
            order by invoice_id, last_update_date desc, raw_id desc
        )
        select company, currency, natural_key, value, cdc_op from latest
        union all select company, currency, natural_key, value, cdc_op from latest_ora
    """,
    ),
    "po_line_value": (
        "po_line",
        "core.po_line",
        "quantity * unit_price",
        """
        with latest as (
            select distinct on (ebeln, ebelp) i.*, 'SAP_LIKE|' || ebeln || '|' || ebelp::integer as natural_key
            from raw.sap_po_item i order by ebeln, ebelp, changed_at desc, raw_id desc
        ), header as (
            select distinct on (ebeln) ebeln, bukrs, waers from raw.sap_po_header order by ebeln, changed_at desc, raw_id desc
        ), latest_ora as (
            select distinct on (po_number, line_num) i.*, 'ORACLE_LIKE|' || po_number || '|' || line_num as natural_key
            from raw.ora_po_lines i order by po_number, line_num, last_update_date desc, raw_id desc
        ), header_ora as (
            select distinct on (po_number) po_number, org_id, currency_code from raw.ora_po_headers
            order by po_number, last_update_date desc, raw_id desc
        )
        select h.bukrs as company, h.waers as currency, l.natural_key, l.menge::numeric * l.netpr::numeric as value, l.cdc_op
        from latest l join header h using (ebeln)
        union all
        select m.company_code, h.currency_code, l.natural_key, l.quantity::numeric * l.unit_price::numeric, l.cdc_op
        from latest_ora l join header_ora h using (po_number) left join stg.org_map m on m.source_org_ref = h.org_id
    """,
    ),
}

CORE_TOTAL_SCOPE = {
    "invoice_net_amount": "select t.company_code as company, t.currency_code as currency, sum(t.net_amount) as total "
    "from core.supplier_invoice t where not t.is_deleted group by 1, 2",
    "po_line_value": "select h.company_code as company, h.currency_code as currency, sum(t.quantity * t.unit_price) as total "
    "from core.po_line t join core.purchase_order h using (source_system, po_number) "
    "where not t.is_deleted group by 1, 2",
}


@dataclass(frozen=True)
class Check:
    name: str
    scope: str
    expected: Decimal | int | None
    actual: Decimal | int | None
    status: str
    detail: str = ""


def _equal(name: str, scope: str, expected, actual, detail: str = "") -> Check:
    return Check(name, scope, expected, actual, "pass" if expected == actual else "fail", detail)


def _row_counts(conn: psycopg.Connection) -> list[Check]:
    """Every current source key is in core or in open quarantine. This is also what catches a row the watermark missed."""
    checks = []
    for entity in ENTITIES:
        keys = ", ".join(entity.key)
        natural = "concat_ws('|', " + ", ".join(f"s.{k}::text" for k in entity.key) + ")"
        row = conn.execute(f"""
            with latest as (
                select distinct on ({keys}) s.*, {natural} as natural_key
                from stg.{entity.name} s order by {keys}, s.source_changed_at desc, s.raw_id desc
            )
            select
                (select count(*) from latest where coalesce(cdc_op, '') <> 'D') as source_rows,
                (select count(*) from {entity.table} where not is_deleted) as core_rows,
                (select count(*) from latest l where coalesce(cdc_op, '') <> 'D' and exists (
                    select 1 from ops.quarantine q where q.entity = '{entity.name}' and q.natural_key = l.natural_key
                    and q.resolved_run_id is null)) as quarantined
        """).fetchone()
        detail = f"{row['quarantined']} in open quarantine" if row["quarantined"] else ""
        checks.append(
            _equal(
                f"row_count.{entity.name}",
                "",
                row["source_rows"] - row["quarantined"],
                row["core_rows"],
                detail,
            )
        )
    return checks


def _control_totals(conn: psycopg.Connection) -> list[Check]:
    checks = []
    for name, (entity, _table, _measure, sql) in CONTROL_TOTALS.items():
        raw_rows = conn.execute(f"""
            select r.company, r.currency,
                   sum(r.value) filter (where q.natural_key is null) as net_of_quarantine,
                   coalesce(sum(r.value) filter (where q.natural_key is not null), 0) as quarantined
            from ({sql}) r
            left join (select distinct natural_key from ops.quarantine
                       where entity = '{entity}' and resolved_run_id is null) q using (natural_key)
            where coalesce(r.cdc_op, '') <> 'D'
            group by 1, 2
        """).fetchall()
        raw = {(r["company"], r["currency"]): r for r in raw_rows}
        core = {
            (r["company"], r["currency"].strip()): r["total"]
            for r in conn.execute(CORE_TOTAL_SCOPE[name]).fetchall()
        }
        for scope in sorted(set(raw) | set(core), key=str):
            source = raw.get(scope)
            expected = (source["net_of_quarantine"] or Decimal(0)) if source else Decimal(0)
            detail = f"quarantined {source['quarantined']}" if source and source["quarantined"] else ""
            checks.append(
                _equal(
                    f"control_total.{name}",
                    f"{scope[0]}/{scope[1]}",
                    expected,
                    core.get(scope, Decimal(0)),
                    detail,
                )
            )
    return checks


def _single_value_checks(conn: psycopg.Connection) -> list[Check]:
    checks = []

    def count(sql: str) -> int:
        return conn.execute(sql).fetchone()["n"]

    checks.append(
        _equal(
            "invoice_header_equals_lines",
            "",
            0,
            count("""
        select count(*) as n from (
            select i.source_system, i.invoice_id from core.supplier_invoice i
            join core.invoice_line l using (source_system, invoice_id)
            where not i.is_deleted and not l.is_deleted
            group by i.source_system, i.invoice_id, i.net_amount
            having i.net_amount <> sum(l.net_amount)
        ) x"""),
        )
    )
    checks.append(
        _equal(
            "every_supplier_has_one_xref",
            "",
            0,
            count("""
        select count(*) as n from core.supplier s
        left join mdm.supplier_xref x using (source_system, source_supplier_id)
        where not s.is_deleted and x.supplier_gid is null"""),
        )
    )
    checks.append(
        _equal(
            "every_invoice_line_has_one_result",
            "",
            0,
            count("""
        select count(*) as n from core.invoice_line l
        left join recon.current_match_result m using (source_system, invoice_id, line_number)
        where not l.is_deleted and m.run_id is null"""),
        )
    )
    for row in conn.execute("""
        select company_code, currency_code,
               sum(amount) filter (where side = 'D') as debit, sum(amount) filter (where side = 'C') as credit
        from recon.current_accrual_journal_line group by 1, 2 order by 1, 2
    """).fetchall():
        checks.append(
            _equal(
                "accrual_journal_balances",
                f"{row['company_code']}/{row['currency_code']}",
                row["debit"],
                row["credit"],
            )
        )
    checks.append(
        _equal(
            "grni_ties_to_journal",
            "",
            0,
            count("""
        select count(*) as n from (
            select company_code, currency_code, sum(grni_amount) as total from recon.current_grni_line group by 1, 2
        ) g join (
            select company_code, currency_code, sum(amount) as total from recon.current_accrual_journal_line where side = 'C' group by 1, 2
        ) j using (company_code, currency_code) where g.total <> j.total"""),
        )
    )

    # The ontology is the source of naming: recon may only write codes the vocabulary defines.
    reasons = {
        r["reason_code"] for r in conn.execute("select distinct reason_code from recon.exception").fetchall()
    }
    reasons |= {
        r["reason_code"]
        for r in conn.execute("select distinct reason_code from recon.control_breach").fetchall()
    }
    statuses = {
        r["status_code"]
        for r in conn.execute("select distinct status_code from recon.match_result").fetchall()
    }
    unknown = sorted((reasons - concept_codes("ExceptionReason")) | (statuses - concept_codes("MatchStatus")))
    checks.append(_equal("codes_are_in_vocabulary", "", 0, len(unknown), ", ".join(unknown)))

    open_quarantine = count("select count(*) as n from ops.quarantine where resolved_run_id is null")
    checks.append(
        Check(
            "open_quarantine",
            "",
            0,
            open_quarantine,
            "pass" if open_quarantine == 0 else "warn",
            "Rows waiting for a parent; retried on every conform run",
        )
    )
    return checks


def _freshness(conn: psycopg.Connection) -> list[Check]:
    # Compared across sources rather than with the clock: batches are synthetic and fixed in time, so wall-clock
    # freshness would fail for ever. A production version would compare with now() and an agreed SLA.
    rows = conn.execute("""
        select source_system, extract(epoch from max(max(extracted_at)) over () - max(extracted_at)) / 3600 as lag_hours
        from ops.ingest_file group by source_system order by source_system
    """).fetchall()
    return [
        Check(
            "source_freshness_lag_hours",
            r["source_system"],
            FRESHNESS_LAG_HOURS,
            r["lag_hours"],
            "pass" if r["lag_hours"] <= FRESHNESS_LAG_HOURS else "warn",
            "Lag behind the newest source; expected is the limit",
        )
        for r in rows
    ]


def run_dq() -> list[Check]:
    with pipeline_run("dq") as run, connect() as conn:
        checks = [*_row_counts(conn), *_control_totals(conn), *_single_value_checks(conn), *_freshness(conn)]
        for check in checks:
            conn.execute(
                "insert into ops.dq_result (run_id, check_name, scope, expected, actual, status, detail) values (%s, %s, %s, %s, %s, %s, %s)",
                (
                    run.run_id,
                    check.name,
                    check.scope,
                    check.expected,
                    check.actual,
                    check.status,
                    check.detail or None,
                ),
            )
        run.counts = {
            status: sum(1 for c in checks if c.status == status) for status in ("pass", "warn", "fail")
        }
        return checks
