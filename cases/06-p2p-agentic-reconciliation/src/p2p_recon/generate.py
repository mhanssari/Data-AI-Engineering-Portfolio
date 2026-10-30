"""Seeded synthetic data for two ERP-shaped sources, with planted scenarios and expected outcomes.

Everything here is invented. Same seed, same bytes: no wall clock, no unordered iteration, so a checksum of the
output is a valid test. Background documents are built to be clean, which makes "no exception outside the planted
scenarios" a meaningful assertion against false positives.

Output: <out>/batch_001 and batch_002 (delimited files plus a manifest), <out>/documents, and
expected_outcomes.csv. See docs/scenarios.md for what each scenario proves.
"""

import csv
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from p2p_recon.config import REFERENCE_DIR

DEFAULT_SEED = 6042
PR_TARGET = 400
SAP, ORA = "SAP_LIKE", "ORACLE_LIKE"
BATCH_1_EXTRACTED = "2026-09-30T22:00:00+00:00"
BATCH_2_EXTRACTED = "2026-10-02T06:00:00+00:00"
CUTOFF = date(2026, 9, 30)
FAR_FUTURE = {SAP: date(9999, 12, 31), ORA: date(4712, 12, 31)}
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# Column order per delivered file. Ingest checks each header against the raw table, so a drift here fails loudly.
COLUMNS = {
    "sap_supplier": "lifnr name1 stras ort01 pstlz land1 stceg bukrs changed_at",
    "sap_supplier_bank": "lifnr iban valid_from changed_at",
    "sap_employee": "pernr ename bukrs appr_limit waers valid_from valid_to changed_at",
    "sap_cost_centre": "kostl bukrs ktext changed_at",
    "sap_gl_account": "saknr bukrs txt50 changed_at",
    "sap_pr": "banfn bukrs afnam lifnr matnr menge preis waers badat kostl saknr changed_at",
    "sap_pr_approval": "banfn step approver approved_at changed_at",
    "sap_po_header": "ebeln bukrs lifnr bedat waers changed_at",
    "sap_po_item": "ebeln ebelp banfn matnr menge meins netpr kostl saknr konnr wepos changed_at",
    "sap_goods_receipt": "mblnr zeile bukrs budat ebeln ebelp menge changed_at",
    "sap_invoice_header": "belnr bukrs lifnr xblnr bldat waers netwr blart rebzg changed_at",
    "sap_invoice_item": "belnr buzei ebeln ebelp menge wrbtr changed_at",
    "ora_suppliers": "vendor_id vendor_name vat_registration_num org_id last_update_date",
    "ora_supplier_sites": "vendor_site_id vendor_id address_line1 city zip country primary_pay_site_flag last_update_date",
    "ora_supplier_bank_accounts": "vendor_id iban start_date last_update_date",
    "ora_employees": "person_id full_name org_id approval_limit currency_code effective_start_date effective_end_date last_update_date",
    "ora_cost_centres": "cost_centre org_id description last_update_date",
    "ora_gl_accounts": "account org_id description last_update_date",
    "ora_requisitions": "requisition_number org_id preparer_id suggested_vendor_id item_code quantity unit_price currency_code creation_date cost_centre account last_update_date",
    "ora_approval_history": "requisition_number sequence_num approver_id action_code action_date last_update_date",
    "ora_po_headers": "po_number org_id vendor_id creation_date currency_code last_update_date",
    "ora_po_lines": "po_number line_num requisition_number item_code quantity uom_code unit_price cost_centre account contract_num match_option last_update_date",
    "ora_receipts": "receipt_num line_num org_id transaction_date po_number po_line_num quantity_received last_update_date",
    "ora_invoices": "invoice_id invoice_num vendor_id org_id invoice_date invoice_currency_code invoice_amount invoice_type_lookup_code credited_invoice_id last_update_date",
    "ora_invoice_lines": "invoice_id line_number po_number po_line_num quantity_invoiced unit_price amount last_update_date",
    "group_legal_entities": "company_code name erp_system source_org_ref currency_code country_code grni_account changed_at",
    "group_items": "item_id description item_type uom changed_at",
    "group_contracts": "contract_id valid_from valid_to company_code erp_system source_supplier_id item_id agreed_price currency_code price_tolerance_pct changed_at",
    "group_policy_parameters": "parameter_code value unit policy_version valid_from changed_at",
    "group_fx_rates": "from_currency to_currency rate valid_from changed_at",
}


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def d(text: str) -> date:
    return date.fromisoformat(text)


def stamp(day: date, hour: int = 8) -> datetime:
    return datetime(day.year, day.month, day.day, hour)


def sap_date(day: date) -> str:
    return day.strftime("%Y%m%d")


def ora_date(day: date) -> str:
    return f"{day.day:02d}-{MONTHS[day.month - 1]}-{day.year}"


def sap_ts(moment: datetime) -> str:
    return moment.strftime("%Y%m%d%H%M%S")


def ora_ts(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


@dataclass(frozen=True)
class Company:
    code: str
    system: str
    currency: str
    org_ref: str
    name: str
    country: str
    grni_account: str


GB = Company("GB01", SAP, "GBP", "GB01", "Fictional Consumer Goods UK Ltd", "GB", "210500")
DE = Company("DE01", ORA, "EUR", "204", "Fictional Konsumgueter Deutschland GmbH", "DE", "2105000")


@dataclass
class Supplier:
    company: Company
    source_id: str
    name: str
    address: str
    city: str
    postcode: str
    country: str
    vat: str | None
    iban: str
    prefix: str
    seq: int = 0

    @property
    def system(self) -> str:
        return self.company.system


@dataclass
class Employee:
    company: Company
    source_id: str
    name: str
    role: str
    limit: Decimal


@dataclass(frozen=True)
class Item:
    item_id: str
    description: str
    kind: str
    uom: str
    price: Decimal


@dataclass
class PR:
    company: Company
    number: str
    requester: Employee
    supplier: Supplier | None
    item: Item
    qty: Decimal
    price: Decimal
    currency: str
    day: date
    cost_centre: str
    gl: str


@dataclass
class POLine:
    company: Company
    po_number: str
    number: int
    pr: PR | None
    item: Item
    qty: Decimal
    price: Decimal
    cost_centre: str
    gl: str
    contract: str | None
    three_way: bool
    changed: datetime


@dataclass
class PO:
    company: Company
    number: str
    supplier: Supplier
    day: date
    currency: str
    lines: list[POLine] = field(default_factory=list)


@dataclass
class Invoice:
    company: Company
    invoice_id: str
    number: str
    supplier: Supplier
    day: date
    currency: str
    lines: int = 0
    amount: Decimal = Decimal("0.00")


class World:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.rows: dict[tuple[int, str], list[dict]] = {}
        self.counters: dict[tuple[str, str], int] = {}
        self.expected: list[dict] = []
        self.suppliers: dict[str, list[Supplier]] = {SAP: [], ORA: []}
        self.reserved: set[str] = set()
        self.employees: dict[str, dict[str, list[Employee]]] = {}
        self.items: list[Item] = []
        self.pr_count = 0
        self.requester_supplier_days: dict[tuple[str, str], list[date]] = {}
        self.documents: dict[str, str] = {}
        self.contracts: list[dict] = []

    # ---------------------------------------------------------------- output helpers
    def emit(self, batch: int, table: str, row: dict, cdc: str | None = None) -> None:
        if batch > 1:
            row = {**row, "cdc_op": cdc or "I"}
        self.rows.setdefault((batch, table), []).append(row)

    def next(self, kind: str, system: str, start: int) -> int:
        key = (kind, system)
        self.counters[key] = self.counters.get(key, start - 1) + 1
        return self.counters[key]

    def expect(
        self,
        sid: str,
        description: str,
        company: str,
        system: str,
        doc_type: str,
        document: str,
        line: int | str,
        status: str,
        reasons: str = "",
        phase: str = "all",
        detail: str = "",
    ) -> None:
        self.expected.append(
            {
                "scenario_id": sid,
                "description": description,
                "company_code": company,
                "source_system": system,
                "document_type": doc_type,
                "document_number": document,
                "line_number": line,
                "expected_status": status,
                "expected_reason_codes": reasons,
                "phase": phase,
                "detail": detail,
            }
        )

    def expect_invoice(
        self,
        sid: str,
        description: str,
        inv: Invoice,
        status: str,
        reasons: str = "",
        phase: str = "all",
        detail: str = "",
    ) -> None:
        for line in range(1, inv.lines + 1):
            self.expect(
                sid,
                description,
                inv.company.code,
                inv.company.system,
                "INVOICE_LINE",
                inv.invoice_id,
                line,
                status,
                reasons,
                phase,
                detail,
            )

    def expect_breach(self, sid: str, description: str, pr: PR, reason: str) -> None:
        self.expect(
            sid,
            description,
            pr.company.code,
            pr.company.system,
            "REQUISITION",
            pr.number,
            "",
            "ControlBreach",
            reason,
        )

    # ---------------------------------------------------------------- master data
    def make_supplier(
        self,
        company: Company,
        name: str,
        address: str,
        city: str,
        postcode: str,
        vat: str | None,
        iban: str | None = None,
        prefix: str | None = None,
        batch: int = 1,
        bank_from: date = date(2024, 3, 1),
    ) -> Supplier:
        number = self.next("supplier", company.system, 100001 if company.system == SAP else 5001)
        source_id = f"{number:010d}" if company.system == SAP else str(number)
        iban = iban or self.make_iban(company)
        supplier = Supplier(
            company,
            source_id,
            name,
            address,
            city,
            postcode,
            company.country,
            vat,
            iban,
            prefix or "".join(w[0] for w in name.split()[:3]).upper(),
        )
        changed = stamp(date(2025, 11, 3) if batch == 1 else date(2026, 10, 1))
        if company.system == SAP:
            self.emit(
                batch,
                "sap_supplier",
                {
                    "lifnr": source_id,
                    "name1": name,
                    "stras": address,
                    "ort01": city,
                    "pstlz": postcode,
                    "land1": company.country,
                    "stceg": vat,
                    "bukrs": company.code,
                    "changed_at": sap_ts(changed),
                },
            )
        else:
            self.emit(
                batch,
                "ora_suppliers",
                {
                    "vendor_id": source_id,
                    "vendor_name": name,
                    "vat_registration_num": vat,
                    "org_id": company.org_ref,
                    "last_update_date": ora_ts(changed),
                },
            )
            self.emit(
                batch,
                "ora_supplier_sites",
                {
                    "vendor_site_id": f"{number}1",
                    "vendor_id": source_id,
                    "address_line1": address,
                    "city": city,
                    "zip": postcode,
                    "country": company.country,
                    "primary_pay_site_flag": "Y",
                    "last_update_date": ora_ts(changed),
                },
            )
        self.add_bank_version(supplier, iban, bank_from, changed, batch)
        self.suppliers[company.system].append(supplier)
        return supplier

    def add_bank_version(
        self, supplier: Supplier, iban: str, valid_from: date, changed: datetime, batch: int = 1
    ) -> None:
        if supplier.system == SAP:
            self.emit(
                batch,
                "sap_supplier_bank",
                {
                    "lifnr": supplier.source_id,
                    "iban": iban,
                    "valid_from": sap_date(valid_from),
                    "changed_at": sap_ts(changed),
                },
            )
        else:
            self.emit(
                batch,
                "ora_supplier_bank_accounts",
                {
                    "vendor_id": supplier.source_id,
                    "iban": iban,
                    "start_date": ora_date(valid_from),
                    "last_update_date": ora_ts(changed),
                },
            )

    def make_iban(self, company: Company) -> str:
        body = "".join(self.rng.choice("0123456789") for _ in range(18))
        return f"{company.country}{self.rng.randint(10, 99)}FICT{body}"

    def make_employee(self, company: Company, name: str, role: str, limit: Decimal) -> Employee:
        number = self.next("employee", company.system, 10001 if company.system == SAP else 3001)
        source_id = f"{number:08d}" if company.system == SAP else str(number)
        employee = Employee(company, source_id, name, role, limit)
        self.add_employee_version(employee, limit, date(2025, 1, 1), FAR_FUTURE[company.system])
        self.employees.setdefault(company.code, {}).setdefault(role, []).append(employee)
        return employee

    def add_employee_version(self, employee: Employee, limit: Decimal, start: date, end: date) -> None:
        company = employee.company
        changed = stamp(start if start.year == 2026 else date(2025, 11, 3))
        if company.system == SAP:
            self.emit(
                1,
                "sap_employee",
                {
                    "pernr": employee.source_id,
                    "ename": employee.name,
                    "bukrs": company.code,
                    "appr_limit": limit,
                    "waers": company.currency,
                    "valid_from": sap_date(start),
                    "valid_to": sap_date(end),
                    "changed_at": sap_ts(changed),
                },
            )
        else:
            self.emit(
                1,
                "ora_employees",
                {
                    "person_id": employee.source_id,
                    "full_name": employee.name,
                    "org_id": company.org_ref,
                    "approval_limit": limit,
                    "currency_code": company.currency,
                    "effective_start_date": ora_date(start),
                    "effective_end_date": ora_date(end),
                    "last_update_date": ora_ts(changed),
                },
            )

    def approver_for(self, company: Company, amount: Decimal, exclude: Employee | None = None) -> Employee:
        for role in ("manager", "head", "director", "finance_director"):
            for person in self.employees[company.code][role]:
                if person.limit >= amount and person is not exclude:
                    return person
        raise ValueError(f"No approver for {amount}")

    # ---------------------------------------------------------------- documents
    def requisition(
        self,
        company: Company,
        requester: Employee,
        supplier: Supplier | None,
        item: Item,
        qty: Decimal,
        price: Decimal,
        day: date,
        approvals: list[tuple[Employee, date]] | None = None,
        currency: str | None = None,
        batch: int = 1,
    ) -> PR:
        system = company.system
        number = str(self.next("pr", system, 10000001 if system == SAP else 20001))
        cost_centre = self.rng.choice(COST_CENTRES[company.code])[0]
        gl = expense_account(company, item)
        pr = PR(
            company,
            number,
            requester,
            supplier,
            item,
            qty,
            price,
            currency or company.currency,
            day,
            cost_centre,
            gl,
        )
        supplier_id = supplier.source_id if supplier else None
        changed = stamp(day)
        if system == SAP:
            self.emit(
                batch,
                "sap_pr",
                {
                    "banfn": number,
                    "bukrs": company.code,
                    "afnam": requester.source_id,
                    "lifnr": supplier_id,
                    "matnr": item.item_id,
                    "menge": qty,
                    "preis": price,
                    "waers": pr.currency,
                    "badat": sap_date(day),
                    "kostl": cost_centre,
                    "saknr": gl,
                    "changed_at": sap_ts(changed),
                },
            )
        else:
            self.emit(
                batch,
                "ora_requisitions",
                {
                    "requisition_number": number,
                    "org_id": company.org_ref,
                    "preparer_id": requester.source_id,
                    "suggested_vendor_id": supplier_id,
                    "item_code": item.item_id,
                    "quantity": qty,
                    "unit_price": price,
                    "currency_code": pr.currency,
                    "creation_date": ora_date(day),
                    "cost_centre": cost_centre,
                    "account": gl,
                    "last_update_date": ora_ts(changed),
                },
            )
            # Oracle records the preparer's submission in the same history as approvals. It must not read as self-approval.
            self.emit(
                batch,
                "ora_approval_history",
                {
                    "requisition_number": number,
                    "sequence_num": 0,
                    "approver_id": requester.source_id,
                    "action_code": "SUBMIT",
                    "action_date": ora_ts(stamp(day, 9)),
                    "last_update_date": ora_ts(stamp(day, 9)),
                },
            )
        if approvals is None:
            amount = money(qty * price)
            approvals = [(self.approver_for(company, amount, exclude=requester), day + timedelta(days=1))]
        for step, (approver, when) in enumerate(approvals, start=1):
            self.approval(pr, step, approver, when, batch)
        if supplier:
            self.requester_supplier_days.setdefault((requester.source_id, supplier.source_id), []).append(day)
        self.pr_count += 1
        return pr

    def approval(self, pr: PR, step: int, approver: Employee, when: date, batch: int = 1) -> None:
        moment = stamp(when, 10)
        if pr.company.system == SAP:
            self.emit(
                batch,
                "sap_pr_approval",
                {
                    "banfn": pr.number,
                    "step": step,
                    "approver": approver.source_id,
                    "approved_at": sap_ts(moment),
                    "changed_at": sap_ts(moment),
                },
            )
        else:
            self.emit(
                batch,
                "ora_approval_history",
                {
                    "requisition_number": pr.number,
                    "sequence_num": step,
                    "approver_id": approver.source_id,
                    "action_code": "APPROVE",
                    "action_date": ora_ts(moment),
                    "last_update_date": ora_ts(moment),
                },
            )

    def purchase_order(
        self, company: Company, supplier: Supplier, day: date, currency: str | None = None, batch: int = 1
    ) -> PO:
        system = company.system
        number = str(self.next("po", system, 4500000001 if system == SAP else 60001))
        po = PO(company, number, supplier, day, currency or company.currency)
        changed = stamp(day)
        if system == SAP:
            self.emit(
                batch,
                "sap_po_header",
                {
                    "ebeln": number,
                    "bukrs": company.code,
                    "lifnr": supplier.source_id,
                    "bedat": sap_date(day),
                    "waers": po.currency,
                    "changed_at": sap_ts(changed),
                },
            )
        else:
            self.emit(
                batch,
                "ora_po_headers",
                {
                    "po_number": number,
                    "org_id": company.org_ref,
                    "vendor_id": supplier.source_id,
                    "creation_date": ora_date(day),
                    "currency_code": po.currency,
                    "last_update_date": ora_ts(changed),
                },
            )
        return po

    def po_line(
        self,
        po: PO,
        pr: PR | None,
        item: Item,
        qty: Decimal,
        price: Decimal,
        contract: str | None = None,
        changed: datetime | None = None,
        batch: int = 1,
        cdc: str | None = None,
        cost_centre: str | None = None,
        number: int | None = None,
    ) -> POLine:
        system = po.company.system
        step = 10 if system == SAP else 1
        number = number or (len(po.lines) + 1) * step
        cost_centre = cost_centre or (
            pr.cost_centre if pr else self.rng.choice(COST_CENTRES[po.company.code])[0]
        )
        gl = pr.gl if pr else expense_account(po.company, item)
        three_way = item.kind == "GOODS"
        changed = changed or stamp(po.day)
        line = POLine(
            po.company, po.number, number, pr, item, qty, price, cost_centre, gl, contract, three_way, changed
        )
        if system == SAP:
            self.emit(
                batch,
                "sap_po_item",
                {
                    "ebeln": po.number,
                    "ebelp": f"{number:05d}",
                    "banfn": pr.number if pr else None,
                    "matnr": item.item_id,
                    "menge": qty,
                    "meins": item.uom,
                    "netpr": price,
                    "kostl": cost_centre,
                    "saknr": gl,
                    "konnr": contract,
                    "wepos": "X" if three_way else None,
                    "changed_at": sap_ts(changed),
                },
                cdc,
            )
        else:
            self.emit(
                batch,
                "ora_po_lines",
                {
                    "po_number": po.number,
                    "line_num": number,
                    "requisition_number": pr.number if pr else None,
                    "item_code": item.item_id,
                    "quantity": qty,
                    "uom_code": item.uom,
                    "unit_price": price,
                    "cost_centre": cost_centre,
                    "account": gl,
                    "contract_num": contract,
                    "match_option": "R" if three_way else "P",
                    "last_update_date": ora_ts(changed),
                },
                cdc,
            )
        po.lines.append(line)
        return line

    def goods_receipt(
        self,
        company: Company,
        day: date,
        lines: list[tuple[POLine, Decimal]],
        changed: datetime | None = None,
        batch: int = 1,
        cdc: str | None = None,
        number: str | None = None,
    ) -> str:
        system = company.system
        number = number or str(self.next("grn", system, 5000000001 if system == SAP else 70001))
        changed = changed or stamp(day)
        for index, (po_line, qty) in enumerate(lines, start=1):
            if system == SAP:
                self.emit(
                    batch,
                    "sap_goods_receipt",
                    {
                        "mblnr": number,
                        "zeile": f"{index:04d}",
                        "bukrs": company.code,
                        "budat": sap_date(day),
                        "ebeln": po_line.po_number,
                        "ebelp": f"{po_line.number:05d}",
                        "menge": qty,
                        "changed_at": sap_ts(changed),
                    },
                    cdc,
                )
            else:
                self.emit(
                    batch,
                    "ora_receipts",
                    {
                        "receipt_num": number,
                        "line_num": index,
                        "org_id": company.org_ref,
                        "transaction_date": ora_date(day),
                        "po_number": po_line.po_number,
                        "po_line_num": po_line.number,
                        "quantity_received": qty,
                        "last_update_date": ora_ts(changed),
                    },
                    cdc,
                )
        return number

    def invoice(
        self,
        company: Company,
        supplier: Supplier,
        day: date,
        lines: list[tuple[POLine | None, Decimal, Decimal]],
        number: str | None = None,
        currency: str | None = None,
        credited: Invoice | str | None = None,
        batch: int = 1,
    ) -> Invoice:
        """Each line is (po line, quantity, net amount), positive even for credit notes: the sign is rendered per source."""
        system = company.system
        invoice_id = str(self.next("invoice", system, 5100000001 if system == SAP else 90001))
        supplier.seq += 1
        number = number or f"{supplier.prefix}-2026-{supplier.seq:04d}"
        currency = currency or company.currency
        is_credit = credited is not None
        credited_id = credited.invoice_id if isinstance(credited, Invoice) else credited
        total = sum((amount for _, _, amount in lines), Decimal("0.00"))
        inv = Invoice(company, invoice_id, number, supplier, day, currency, len(lines), total)
        changed = stamp(day)
        if system == SAP:
            self.emit(
                batch,
                "sap_invoice_header",
                {
                    "belnr": invoice_id,
                    "bukrs": company.code,
                    "lifnr": supplier.source_id,
                    "xblnr": number,
                    "bldat": sap_date(day),
                    "waers": currency,
                    "netwr": total,
                    "blart": "KG" if is_credit else "RE",
                    "rebzg": credited_id,
                    "changed_at": sap_ts(changed),
                },
            )
        else:
            sign = Decimal("-1") if is_credit else Decimal("1")
            self.emit(
                batch,
                "ora_invoices",
                {
                    "invoice_id": invoice_id,
                    "invoice_num": number,
                    "vendor_id": supplier.source_id,
                    "org_id": company.org_ref,
                    "invoice_date": ora_date(day),
                    "invoice_currency_code": currency,
                    "invoice_amount": sign * total,
                    "invoice_type_lookup_code": "CREDIT" if is_credit else "STANDARD",
                    "credited_invoice_id": credited_id,
                    "last_update_date": ora_ts(changed),
                },
            )
        for index, (po_line, qty, amount) in enumerate(lines, start=1):
            if system == SAP:
                self.emit(
                    batch,
                    "sap_invoice_item",
                    {
                        "belnr": invoice_id,
                        "buzei": f"{index:03d}",
                        "ebeln": po_line.po_number if po_line else None,
                        "ebelp": f"{po_line.number:05d}" if po_line else None,
                        "menge": qty,
                        "wrbtr": amount,
                        "changed_at": sap_ts(changed),
                    },
                )
            else:
                sign = Decimal("-1") if is_credit else Decimal("1")
                self.emit(
                    batch,
                    "ora_invoice_lines",
                    {
                        "invoice_id": invoice_id,
                        "line_number": index,
                        "po_number": po_line.po_number if po_line else None,
                        "po_line_num": po_line.number if po_line else None,
                        "quantity_invoiced": sign * qty,
                        "unit_price": (amount / qty).quantize(Decimal("0.000001")),
                        "amount": sign * amount,
                        "last_update_date": ora_ts(changed),
                    },
                )
        return inv

    def contract_version(
        self,
        contract_id: str,
        company: Company,
        supplier: Supplier,
        item: Item,
        price: Decimal,
        tolerance: Decimal,
        start: date,
        end: date,
    ) -> None:
        self.emit(
            1,
            "group_contracts",
            {
                "contract_id": contract_id,
                "valid_from": start.isoformat(),
                "valid_to": end.isoformat(),
                "company_code": company.code,
                "erp_system": company.system,
                "source_supplier_id": supplier.source_id,
                "item_id": item.item_id,
                "agreed_price": price,
                "currency_code": company.currency,
                "price_tolerance_pct": tolerance,
                "changed_at": "2025-11-03T08:00:00",
            },
        )
        self.contracts.append(
            {
                "id": contract_id,
                "company": company,
                "supplier": supplier,
                "item": item,
                "price": price,
                "tolerance": tolerance,
                "start": start,
                "end": end,
            }
        )

    def chain(
        self,
        company: Company,
        supplier: Supplier,
        item: Item,
        qty: Decimal,
        price: Decimal,
        start: date,
        requester: Employee | None = None,
        approvals: list[tuple[Employee, date]] | None = None,
        contract: str | None = None,
        currency: str | None = None,
        batch: int = 1,
    ) -> tuple[PR, PO, POLine]:
        """Requisition, approval and PO, which every scenario starts from."""
        requester = requester or self.employees[company.code]["requester"][0]
        pr = self.requisition(
            company, requester, supplier, item, qty, price, start, approvals, currency, batch
        )
        po = self.purchase_order(company, supplier, start + timedelta(days=3), currency, batch)
        line = self.po_line(po, pr, item, qty, price, contract, batch=batch)
        return pr, po, line


COST_CENTRES = {
    "GB01": [
        ("UK1000", "UK Manufacturing"),
        ("UK2000", "UK Logistics"),
        ("UK3000", "UK Marketing"),
        ("UK4000", "UK Facilities"),
        ("UK5000", "UK IT"),
    ],
    "DE01": [
        ("DE-100", "Werk Hamburg"),
        ("DE-200", "Logistik Nord"),
        ("DE-300", "Marketing DACH"),
        ("DE-400", "Gebaeudeservice"),
        ("DE-500", "IT Services"),
    ],
}
GL_ACCOUNTS = {
    "GB01": [
        ("500100", "Raw materials and packaging"),
        ("500200", "Consumables"),
        ("620100", "Professional services"),
        ("210500", "GRNI clearing"),
    ],
    "DE01": [
        ("5000100", "Rohstoffe und Verpackung"),
        ("5000200", "Verbrauchsmaterial"),
        ("6200100", "Dienstleistungen"),
        ("2105000", "WE/RE Verrechnung"),
    ],
}


def expense_account(company: Company, item: Item) -> str:
    accounts = GL_ACCOUNTS[company.code]
    return accounts[2][0] if item.kind == "SERVICE" else accounts[0 if item.item_id < "ITM-012" else 1][0]


def build_reference(world: World, policy: dict) -> None:
    changed = "2025-11-03T08:00:00"
    for company in (GB, DE):
        world.emit(
            1,
            "group_legal_entities",
            {
                "company_code": company.code,
                "name": company.name,
                "erp_system": company.system,
                "source_org_ref": company.org_ref,
                "currency_code": company.currency,
                "country_code": company.country,
                "grni_account": company.grni_account,
                "changed_at": changed,
            },
        )
        for code, text in COST_CENTRES[company.code]:
            if company.system == SAP:
                world.emit(
                    1,
                    "sap_cost_centre",
                    {"kostl": code, "bukrs": company.code, "ktext": text, "changed_at": "20251103080000"},
                )
            else:
                world.emit(
                    1,
                    "ora_cost_centres",
                    {
                        "cost_centre": code,
                        "org_id": company.org_ref,
                        "description": text,
                        "last_update_date": changed,
                    },
                )
        for code, text in GL_ACCOUNTS[company.code]:
            if company.system == SAP:
                world.emit(
                    1,
                    "sap_gl_account",
                    {"saknr": code, "bukrs": company.code, "txt50": text, "changed_at": "20251103080000"},
                )
            else:
                world.emit(
                    1,
                    "ora_gl_accounts",
                    {
                        "account": code,
                        "org_id": company.org_ref,
                        "description": text,
                        "last_update_date": changed,
                    },
                )

    goods = [
        "Corrugated cartons",
        "Shrink film",
        "Glass jars",
        "Aluminium caps",
        "Printed labels",
        "Citric acid",
        "Sunflower oil",
        "Cane sugar",
        "Oat flour",
        "Cocoa powder",
        "Sea salt",
        "Pallet wrap",
        "Cleaning concentrate",
        "Safety gloves",
        "Ink cartridges",
        "Lubricant",
        "Filter cartridges",
        "Conveyor belts",
        "Spare seals",
        "Hairnets",
    ]
    services = [
        "Equipment maintenance",
        "Consultancy day",
        "Temporary staffing",
        "Waste collection",
        "Pest control",
        "Calibration service",
    ]
    for index, name in enumerate(goods, start=1):
        price = Decimal(world.rng.randint(150, 9000)) / 100
        world.items.append(Item(f"ITM-{index:03d}", name, "GOODS", "EA", price))
    for index, name in enumerate(services, start=21):
        price = Decimal(world.rng.randint(5000, 30000)) / 100
        world.items.append(Item(f"ITM-{index:03d}", name, "SERVICE", "H", price))
    for item in world.items:
        world.emit(
            1,
            "group_items",
            {
                "item_id": item.item_id,
                "description": item.description,
                "item_type": item.kind,
                "uom": item.uom,
                "changed_at": changed,
            },
        )

    for code, parameter in policy["parameters"].items():
        world.emit(
            1,
            "group_policy_parameters",
            {
                "parameter_code": code,
                "value": parameter["value"],
                "unit": parameter["unit"],
                "policy_version": policy["policy_version"],
                "valid_from": policy["valid_from"],
                "changed_at": changed,
            },
        )
    for from_ccy, to_ccy, rate in (("EUR", "GBP", "0.870000"), ("GBP", "EUR", "1.150000")):
        world.emit(
            1,
            "group_fx_rates",
            {
                "from_currency": from_ccy,
                "to_currency": to_ccy,
                "rate": rate,
                "valid_from": "2026-01-01",
                "changed_at": changed,
            },
        )

    # Names are invented and deliberately plain; each place name is used once so that background suppliers do not
    # accidentally look alike and pollute the steward queue.
    for company, count_people in (
        (
            GB,
            [
                "Alex Morgan",
                "Sam Patel",
                "Jo Evans",
                "Chris Lowe",
                "Pat Hughes",
                "Robin Shaw",
                "Dana Reid",
                "Lee Foster",
                "Jamie Cole",
                "Kim Barnes",
            ],
        ),
        (
            DE,
            [
                "Anna Becker",
                "Lukas Vogel",
                "Mia Krause",
                "Jonas Lang",
                "Lea Braun",
                "Finn Otto",
                "Emma Sauer",
                "Paul Winter",
                "Nina Roth",
                "Tom Keller",
            ],
        ),
    ):
        for name in count_people[:6]:
            world.make_employee(company, name, "requester", Decimal("0.00"))
        for name in count_people[6:8]:
            world.make_employee(company, name, "manager", Decimal("5000.00"))
        world.make_employee(company, count_people[8], "head", Decimal("25000.00"))
        world.make_employee(company, count_people[9], "director", Decimal("100000.00"))
        world.make_employee(
            company, f"{count_people[0].split()[0]} FD", "finance_director", Decimal("1000000.00")
        )


def build_suppliers(world: World) -> dict[str, tuple[Supplier, Supplier]]:
    """Eight planted pairs, then background suppliers up to 60 in total. Pair keys are used by the scenarios."""
    pairs: dict[str, tuple[Supplier, Supplier]] = {}
    m = world.make_supplier
    pairs["exact_vat"] = (
        m(GB, "Brightwell Packaging Ltd", "14 Mill Lane", "Leeds", "LS1 4AB", "GB100200301", prefix="BW"),
        m(
            DE,
            "BRIGHTWELL PACKAGING LIMITED",
            "Hafenstrasse 8",
            "Hamburg",
            "20457",
            "GB 100 200 301",
            prefix="BWP",
        ),
    )
    pairs["exact_vat_2"] = (
        m(GB, "Corvid Ingredients Ltd", "2 Quay Road", "Hull", "HU1 2CD", "GB100200402"),
        m(DE, "Corvid Ingredients", "Elbchaussee 21", "Hamburg", "22765", "GB100200402"),
    )
    shared_iban = world.make_iban(DE)
    pairs["exact_bank"] = (
        m(GB, "Rheintal Logistik Ltd", "9 Dock Street", "Felixstowe", "IP11 3EF", None, iban=shared_iban),
        m(
            DE,
            "Rheintal Logistik GmbH",
            "Rheinuferstrasse 40",
            "Koeln",
            "50668",
            "DE300400501",
            iban=shared_iban,
        ),
    )
    pairs["fuzzy_auto"] = (
        m(GB, "Kestrel Industrial Supplies Ltd", "77 Foundry Road", "Sheffield", "S1 5GH", None),
        m(DE, "Kestrel Industrial Supplies GmbH", "77 Foundry Road", "Sheffield", "S1 5GH", None),
    )
    pairs["review_band"] = (
        m(GB, "Oakfield Ingredients Ltd", "5 Harbour View", "Bristol", "BS1 6JK", None),
        m(DE, "Oakfield Food Ingredients GmbH", "Bahnhofstrasse 3", "Bremen", "28195", None),
    )
    pairs["vat_conflict"] = (
        m(GB, "Atlas Chemicals Ltd", "31 Canal Street", "Manchester", "M1 7LM", "GB100200703"),
        m(DE, "Atlas Chemicals GmbH", "31 Canal Street", "Manchester", "M1 7LM", "DE300400704"),
    )
    pairs["different"] = (
        m(GB, "Northern Packaging Ltd", "18 Wharf Road", "Newcastle", "NE1 8NP", "GB100200805"),
        m(DE, "Northern Paper Handel GmbH", "Industrieweg 12", "Kiel", "24103", "DE300400806"),
    )
    fraud_iban = world.make_iban(GB)
    pairs["shared_bank"] = (
        m(GB, "Tern Consulting Ltd", "6 Market Square", "York", "YO1 9QR", "GB100200907", iban=fraud_iban),
        m(DE, "Marlow Reinigung GmbH", "Lindenallee 90", "Luebeck", "23552", "DE300400908", iban=fraud_iban),
    )

    uk_places = [
        "Harwood",
        "Ashdown",
        "Calder",
        "Fenwick",
        "Glenmore",
        "Hexley",
        "Ingleby",
        "Jarrow",
        "Kelso",
        "Lydney",
        "Malvern",
        "Nairn",
        "Otley",
        "Penrith",
        "Quorn",
        "Ripon",
        "Skipton",
        "Tenby",
        "Ulverston",
        "Ventnor",
        "Whitby",
        "Yarrow",
    ]
    de_places = [
        "Altona",
        "Bergedorf",
        "Celle",
        "Detmold",
        "Emden",
        "Flensburg",
        "Goslar",
        "Hameln",
        "Itzehoe",
        "Jena",
        "Kassel",
        "Lingen",
        "Minden",
        "Nienburg",
        "Oldenburg",
        "Paderborn",
        "Quedlinburg",
        "Rostock",
        "Stade",
        "Trier",
        "Uelzen",
        "Verden",
    ]
    uk_trades = [
        "Metals",
        "Labels",
        "Plastics",
        "Foods",
        "Freight",
        "Print",
        "Cleaning",
        "Engineering",
        "Glass",
        "Textiles",
        "Pallets",
    ]
    de_trades = [
        "Metallbau",
        "Etiketten",
        "Kunststoff",
        "Nahrung",
        "Spedition",
        "Druck",
        "Reinigung",
        "Technik",
        "Glaswerk",
        "Textil",
        "Paletten",
    ]
    for index, place in enumerate(uk_places):
        m(
            GB,
            f"{place} {uk_trades[index % 11]} Ltd",
            f"{world.rng.randint(1, 200)} {place} Road",
            place,
            f"{place[:2].upper()}{index % 9 + 1} {index % 9 + 1}ZZ",
            f"GB2{index:02d}0000{index:02d}",
        )
    for index, place in enumerate(de_places):
        m(
            DE,
            f"{place} {de_trades[index % 11]} GmbH",
            f"{place}er Strasse {world.rng.randint(1, 200)}",
            place,
            f"{30000 + index * 137}",
            f"DE4{index:02d}0000{index:02d}",
        )
    # Older bank changes on a few suppliers, well outside the window: the control must not fire on these.
    for supplier in world.suppliers[SAP][16:20] + world.suppliers[ORA][16:20]:
        world.add_bank_version(
            supplier, world.make_iban(supplier.company), date(2025, 12, 15), stamp(date(2025, 12, 15))
        )
    return pairs


def build_scenarios(world: World, pairs: dict[str, tuple[Supplier, Supplier]]) -> None:
    w = world
    emp = w.employees
    goods = [i for i in w.items if i.kind == "GOODS"]
    services = [i for i in w.items if i.kind == "SERVICE"]
    gb_sup = w.suppliers[SAP][8:]
    de_sup = w.suppliers[ORA][8:]
    q = Decimal
    gb_req = emp["GB01"]["requester"]
    de_req = emp["DE01"]["requester"]

    def rec(company: Company, day: date, line: POLine, qty: Decimal | None = None, **kwargs) -> str:
        return w.goods_receipt(company, day, [(line, qty if qty is not None else line.qty)], **kwargs)

    # Scenarios use round quantities and prices so that the amounts can be checked by eye.
    pr, po, line = w.chain(GB, gb_sup[0], goods[0], q("100.000"), q("10.000000"), d("2026-03-02"))
    rec(GB, d("2026-03-10"), line)
    inv = w.invoice(GB, gb_sup[0], d("2026-03-15"), [(line, q("100"), q("1000.00"))])
    w.expect_invoice("S01", "Perfect three-way match", inv, "Matched")

    pr, po, line = w.chain(DE, de_sup[0], goods[1], q("100.000"), q("20.000000"), d("2026-03-03"))
    rec(DE, d("2026-03-11"), line)
    inv = w.invoice(DE, de_sup[0], d("2026-03-16"), [(line, q("100"), q("2030.00"))])
    w.expect_invoice(
        "S02", "Price variance within the default 2% tolerance (1.5%)", inv, "MatchedWithinTolerance"
    )

    pr, po, line = w.chain(GB, gb_sup[1], goods[2], q("100.000"), q("10.000000"), d("2026-03-04"))
    rec(GB, d("2026-03-12"), line)
    inv = w.invoice(GB, gb_sup[1], d("2026-03-17"), [(line, q("100"), q("1050.00"))])
    w.expect_invoice("S03", "Price variance above tolerance (5%)", inv, "Exception", "PriceVariance")

    pr, po, line = w.chain(DE, de_sup[1], goods[3], q("120.000"), q("10.000000"), d("2026-03-05"))
    rec(DE, d("2026-03-13"), line, q("100.000"))
    inv = w.invoice(DE, de_sup[1], d("2026-03-18"), [(line, q("120"), q("1200.00"))])
    w.expect_invoice("S04", "Quantity invoiced greater than received", inv, "Exception", "QuantityVariance")

    pr, po, line = w.chain(GB, gb_sup[2], goods[4], q("50.000"), q("10.000000"), d("2026-03-09"))
    rec(GB, d("2026-03-25"), line)
    inv = w.invoice(GB, gb_sup[2], d("2026-03-20"), [(line, q("50"), q("500.00"))])
    w.expect_invoice(
        "S05", "Invoice dated before the goods receipt", inv, "Exception", "InvoiceBeforeReceipt"
    )

    pr, po, line = w.chain(DE, de_sup[2], goods[5], q("100.000"), q("15.000000"), d("2026-03-10"))
    rec(DE, d("2026-03-20"), line, q("60.000"))
    inv = w.invoice(DE, de_sup[2], d("2026-03-25"), [(line, q("60"), q("900.00"))])
    w.expect_invoice("S06", "Partial receipt with a matching partial invoice", inv, "Matched")

    pr, po, line = w.chain(GB, gb_sup[3], goods[6], q("80.000"), q("25.000000"), d("2026-03-11"))
    rec(GB, d("2026-03-19"), line)
    original = w.invoice(
        GB, gb_sup[3], d("2026-03-24"), [(line, q("80"), q("2000.00"))], number="HL-2026-0045"
    )
    duplicate = w.invoice(
        GB, gb_sup[3], d("2026-04-02"), [(line, q("80"), q("2000.00"))], number="hl/2026/0045"
    )
    w.expect_invoice("S07", "Original of a duplicated invoice", original, "Matched")
    w.expect_invoice(
        "S07", "Duplicate invoice with a reformatted number", duplicate, "Exception", "DuplicateInvoice"
    )

    inv = w.invoice(DE, de_sup[3], d("2026-03-26"), [(None, q("1"), q("750.00"))])
    w.expect_invoice("S08", "Invoice with no purchase order", inv, "Exception", "NoPurchaseOrder")

    pr, po, line = w.chain(GB, gb_sup[4], goods[7], q("40.000"), q("30.000000"), d("2026-04-06"))
    rec(GB, d("2026-04-07"), line)
    inv = w.invoice(GB, gb_sup[4], d("2026-04-08"), [(line, q("40"), q("1200.00"))])
    w.expect_invoice("S09", "Purchase order dated after the invoice", inv, "Exception", "RetrospectivePO")

    # Three requisitions of 4,000 each: each is within the 5,000 tier, together they are not.
    manager = emp["DE01"]["manager"][0]
    for offset in (0, 2, 5):
        start = d("2026-04-13") + timedelta(days=offset)
        pr, po, line = w.chain(
            DE,
            de_sup[4],
            goods[8],
            q("200.000"),
            q("20.000000"),
            start,
            requester=de_req[1],
            approvals=[(manager, start + timedelta(days=1))],
        )
        rec(DE, start + timedelta(days=10), line)
        inv = w.invoice(DE, de_sup[4], start + timedelta(days=15), [(line, q("200"), q("4000.00"))])
        w.expect_breach("S10", "Requisitions split to stay under an approval tier", pr, "SplitRequisition")
        w.expect_invoice("S10", "Invoice funded by a split requisition", inv, "Exception", "SplitRequisition")

    manager = emp["GB01"]["manager"][0]
    pr, po, line = w.chain(
        GB,
        gb_sup[5],
        goods[9],
        q("400.000"),
        q("30.000000"),
        d("2026-04-14"),
        approvals=[(manager, d("2026-04-15"))],
    )
    rec(GB, d("2026-04-22"), line)
    inv = w.invoice(GB, gb_sup[5], d("2026-04-27"), [(line, q("400"), q("12000.00"))])
    w.expect_breach("S11", "Approver above their approval limit", pr, "ApprovalLimitExceeded")
    w.expect_invoice(
        "S11", "Invoice funded by an over-limit approval", inv, "Exception", "ApprovalLimitExceeded"
    )

    head = emp["DE01"]["head"][0]
    pr, po, line = w.chain(
        DE,
        de_sup[5],
        goods[10],
        q("100.000"),
        q("30.000000"),
        d("2026-04-15"),
        requester=head,
        approvals=[(head, d("2026-04-16"))],
    )
    rec(DE, d("2026-04-23"), line)
    inv = w.invoice(DE, de_sup[5], d("2026-04-28"), [(line, q("100"), q("3000.00"))])
    w.expect_breach("S12", "Requester approved their own requisition", pr, "SegregationOfDuties")
    w.expect_invoice(
        "S12", "Invoice funded by a self-approved requisition", inv, "Exception", "SegregationOfDuties"
    )

    resolutions = {
        "exact_vat": ("Merged", "EXACT_VAT", "Same VAT number, formatted differently, name variant"),
        "exact_vat_2": ("Merged", "EXACT_VAT", "Same VAT number, different address"),
        "exact_bank": ("Merged", "EXACT_BANK", "Same bank account and a similar name"),
        "fuzzy_auto": ("Merged", "FUZZY", "No VAT or bank evidence; near-identical name and address"),
        "review_band": ("StewardReview", "FUZZY", "Similar name, different address: a person must decide"),
        "vat_conflict": (
            "StewardReview",
            "VAT_CONFLICT",
            "Same name and address but conflicting VAT numbers: never auto-merge",
        ),
        "different": ("NotMerged", "", "Similar-looking, genuinely different suppliers"),
        "shared_bank": (
            "StewardReview",
            "SHARED_BANK",
            "Same bank account, unrelated names: a fraud signal, not a merge",
        ),
    }
    for key, (status, method, note) in resolutions.items():
        a, b = pairs[key]
        w.expect(
            "S13",
            note,
            "GB01,DE01",
            "BOTH",
            "SUPPLIER_PAIR",
            f"{a.system}:{a.source_id}|{b.system}:{b.source_id}",
            "",
            status,
            method,
        )

    # S14: the same supplier, an invoice before the change (clean) and one after it (held).
    target = gb_sup[6]
    w.reserved.add(target.source_id)
    pr, po, line = w.chain(GB, target, goods[11], q("200.000"), q("5.000000"), d("2026-04-20"))
    rec(GB, d("2026-04-28"), line, q("100.000"))
    before = w.invoice(GB, target, d("2026-05-02"), [(line, q("100"), q("500.00"))])
    w.add_bank_version(target, w.make_iban(GB), d("2026-05-10"), stamp(d("2026-05-10")))
    rec(GB, d("2026-05-15"), line, q("100.000"))
    after = w.invoice(GB, target, d("2026-05-20"), [(line, q("100"), q("500.00"))])
    w.expect_invoice("S14", "Invoice before the bank change is unaffected", before, "Matched")
    w.expect_invoice(
        "S14", "Bank details changed 10 days before the invoice", after, "Exception", "BankDetailChange"
    )
    w.documents["email_S14_bank_change.md"] = (
        f"# Supplier email (synthetic)\n\nFrom: accounts@{target.prefix.lower()}.example\nDate: 9 May 2026\nSubject: New bank details\n\n"
        f"Please update our bank account with immediate effect for all open invoices. Supplier reference {target.source_id}. "
        "We are unable to take calls this week, so please reply by email only.\n"
    )

    pr, po, line = w.chain(DE, de_sup[7], goods[12], q("100.000"), q("10.000000"), d("2026-04-21"))
    rec(DE, d("2026-04-29"), line)
    inv = w.invoice(DE, de_sup[7], d("2026-05-04"), [(line, q("100"), q("1000.00"))], currency="GBP")
    w.expect_invoice(
        "S15", "Invoice currency differs from the PO currency", inv, "Exception", "CurrencyMismatch"
    )

    pr, po, line = w.chain(GB, gb_sup[7], goods[13], q("100.000"), q("10.000000"), d("2026-04-22"))
    rec(GB, d("2026-04-30"), line)
    original = w.invoice(GB, gb_sup[7], d("2026-05-05"), [(line, q("100"), q("1000.00"))])
    credit = w.invoice(GB, gb_sup[7], d("2026-05-12"), [(line, q("20"), q("200.00"))], credited=original)
    w.expect_invoice("S16", "Invoice later part-credited", original, "Matched")
    w.expect_invoice("S16", "Credit note against a PO, matched to its original invoice", credit, "Matched")
    w.expect(
        "S16",
        "A valid credit note reopens the quantity it credits",
        "GB01",
        SAP,
        "PO_LINE",
        po.number,
        line.number,
        "InGRNI",
        "",
        detail="grni_quantity=20.000;grni_amount=200.00",
    )
    w.documents["note_S16_credit.md"] = (
        f"# Supplier remittance note (synthetic)\n\nCredit note {credit.number} reduces invoice {original.number} by {GB.currency} 200.00: "
        "20 units were returned as damaged.\n"
    )

    # S17: the receipt was keyed short; a correction arrives in the next change log.
    pr, po, line = w.chain(DE, de_sup[8], goods[14], q("100.000"), q("10.000000"), d("2026-08-10"))
    grn = rec(DE, d("2026-08-18"), line, q("80.000"))
    inv = w.invoice(DE, de_sup[8], d("2026-08-25"), [(line, q("100"), q("1000.00"))])
    w.goods_receipt(
        DE,
        d("2026-08-18"),
        [(line, q("100.000"))],
        changed=stamp(d("2026-10-01")),
        batch=2,
        cdc="U",
        number=grn,
    )
    w.expect_invoice(
        "S17",
        "Before correction: receipt short of the invoice",
        inv,
        "Exception",
        "QuantityVariance",
        phase="batch_001",
    )
    w.expect_invoice("S17", "After the late receipt correction", inv, "Matched", phase="final")

    pr, po, line = w.chain(GB, gb_sup[8], services[0], q("40.000"), q("100.000000"), d("2026-05-04"))
    inv = w.invoice(GB, gb_sup[8], d("2026-05-20"), [(line, q("40"), q("4000.00"))])
    w.expect_invoice("S18", "Service invoice, two-way match", inv, "Matched")

    pr, po, line = w.chain(DE, de_sup[9], goods[15], q("70.000"), q("12.500000"), d("2026-09-07"))
    rec(DE, d("2026-09-20"), line)
    w.expect(
        "S19",
        "Goods received, not invoiced at cut-off",
        "DE01",
        ORA,
        "PO_LINE",
        po.number,
        line.number,
        "InGRNI",
        "",
        detail="grni_quantity=70.000;grni_amount=875.00",
    )

    # S20: the whole chain arrives before its supplier master record; quarantine clears when the supplier does.
    late_supplier = w.suppliers[ORA].pop()
    w.reserved.add(late_supplier.source_id)
    for table in ("ora_suppliers", "ora_supplier_sites", "ora_supplier_bank_accounts"):
        moved = [r for r in w.rows[(1, table)] if r["vendor_id"] == late_supplier.source_id]
        w.rows[(1, table)] = [r for r in w.rows[(1, table)] if r["vendor_id"] != late_supplier.source_id]
        for row in moved:
            row = dict(row)
            row["last_update_date"] = "2026-10-01T08:00:00"
            w.emit(2, table, row, "I")
    w.suppliers[ORA].append(late_supplier)
    pr, po, line = w.chain(DE, late_supplier, goods[16], q("10.000"), q("50.000000"), d("2026-09-01"))
    rec(DE, d("2026-09-08"), line)
    inv = w.invoice(DE, late_supplier, d("2026-09-14"), [(line, q("10"), q("500.00"))])
    w.expect_invoice("S20", "Supplier master not delivered yet", inv, "Quarantined", phase="batch_001")
    w.expect_invoice("S20", "Supplier arrived; quarantine cleared", inv, "Matched", phase="final")

    # S21: the same invoice sent to both ERPs. Only the golden supplier links them. The UK requisition is in EUR:
    # 5,500 EUR is 4,785 GBP, within a 5,000 GBP manager limit only if the amount is converted, which proves the FX step.
    a, b = pairs["exact_vat"]
    manager_gb = emp["GB01"]["manager"][0]
    head_de = emp["DE01"]["head"][0]
    pr, po, line = w.chain(
        GB,
        a,
        goods[17],
        q("100.000"),
        q("55.000000"),
        d("2026-06-01"),
        currency="EUR",
        approvals=[(manager_gb, d("2026-06-02"))],
    )
    rec(GB, d("2026-06-10"), line)
    first = w.invoice(
        GB, a, d("2026-06-15"), [(line, q("100"), q("5500.00"))], number="BW-2026-0117", currency="EUR"
    )
    pr, po, line = w.chain(
        DE,
        b,
        goods[17],
        q("100.000"),
        q("55.000000"),
        d("2026-06-02"),
        approvals=[(head_de, d("2026-06-03"))],
    )
    rec(DE, d("2026-06-11"), line)
    second = w.invoice(DE, b, d("2026-06-22"), [(line, q("100"), q("5500.00"))], number="BW/2026/0117")
    w.expect_invoice("S21", "First delivery of an invoice sent to both ERPs", first, "Matched")
    w.expect_invoice(
        "S21",
        "Same invoice in the other ERP, caught through the golden supplier",
        second,
        "Exception",
        "DuplicateInvoice",
    )

    pr, po, line = w.chain(DE, de_sup[10], goods[18], q("50.000"), q("10.000000"), d("2026-05-11"))
    rec(DE, d("2026-05-19"), line)
    original = w.invoice(DE, de_sup[10], d("2026-05-26"), [(line, q("50"), q("500.00"))])
    credit = w.invoice(DE, de_sup[10], d("2026-06-02"), [(line, q("60"), q("600.00"))], credited=original)
    w.expect_invoice(
        "S22", "Credit note exceeding the original invoice", credit, "Exception", "UnmatchedCreditNote"
    )
    w.expect(
        "S22",
        "A rejected credit note does not create an accrual",
        "DE01",
        ORA,
        "PO_LINE",
        po.number,
        line.number,
        "NotInGRNI",
    )

    # S23: contract tolerance overrides the default, as of the invoice date: 1% up to March, 5% from April.
    supplier_c = gb_sup[9]
    item_c = goods[19]
    w.contract_version(
        "CT-2026-001", GB, supplier_c, item_c, q("10.000000"), q("1.0000"), d("2026-01-01"), d("2026-03-31")
    )
    w.contract_version(
        "CT-2026-001", GB, supplier_c, item_c, q("10.000000"), q("5.0000"), d("2026-04-01"), d("2026-12-31")
    )
    pr, po, line = w.chain(
        GB, supplier_c, item_c, q("100.000"), q("10.000000"), d("2026-02-02"), contract="CT-2026-001"
    )
    rec(GB, d("2026-02-10"), line)
    tight = w.invoice(GB, supplier_c, d("2026-02-20"), [(line, q("100"), q("1015.00"))])
    w.expect_invoice(
        "S23",
        "1.5% is above the 1% contract tolerance, though within the 2% default",
        tight,
        "Exception",
        "PriceVariance",
    )
    pr, po, line = w.chain(
        GB, supplier_c, item_c, q("100.000"), q("10.000000"), d("2026-05-04"), contract="CT-2026-001"
    )
    rec(GB, d("2026-05-12"), line)
    loose = w.invoice(GB, supplier_c, d("2026-05-20"), [(line, q("100"), q("1040.00"))])
    w.expect_invoice(
        "S23",
        "4% is within the later 5% contract version, though above the 2% default",
        loose,
        "MatchedWithinTolerance",
    )

    pr, po, line = w.chain(
        DE, de_sup[11], goods[0], q("10.000"), q("10.000000"), d("2026-05-12"), approvals=[]
    )
    rec(DE, d("2026-05-20"), line)
    inv = w.invoice(DE, de_sup[11], d("2026-05-27"), [(line, q("10"), q("100.00"))])
    w.expect_breach("S24", "Converted to a PO with no approval", pr, "MissingApproval")
    w.expect_invoice(
        "S24", "Invoice funded by an unapproved requisition", inv, "Exception", "MissingApproval"
    )

    po = w.purchase_order(GB, gb_sup[10], d("2026-05-13"))
    line = w.po_line(po, None, goods[1], q("10.000"), q("10.000000"))
    rec(GB, d("2026-05-21"), line)
    inv = w.invoice(GB, gb_sup[10], d("2026-05-28"), [(line, q("10"), q("100.00"))])
    w.expect_invoice("S25", "Purchase order with no requisition", inv, "Exception", "MissingRequisition")

    # S26: the approver's limit was raised after the approval. A control reading today's limit would wrongly pass this.
    late_manager = emp["DE01"]["manager"][1]
    w.rows[(1, "ora_employees")] = [
        {**r, "effective_end_date": ora_date(d("2026-06-30"))}
        if r["person_id"] == late_manager.source_id
        else r
        for r in w.rows[(1, "ora_employees")]
    ]
    w.add_employee_version(late_manager, q("25000.00"), d("2026-07-01"), FAR_FUTURE[ORA])
    pr, po, line = w.chain(
        DE,
        de_sup[12],
        goods[2],
        q("600.000"),
        q("20.000000"),
        d("2026-05-18"),
        approvals=[(late_manager, d("2026-05-19"))],
    )
    rec(DE, d("2026-05-27"), line)
    inv = w.invoice(DE, de_sup[12], d("2026-06-03"), [(line, q("600"), q("12000.00"))])
    w.expect_breach(
        "S26", "Over the limit in force at approval, though within today's limit", pr, "ApprovalLimitExceeded"
    )
    w.expect_invoice("S26", "Invoice funded by that approval", inv, "Exception", "ApprovalLimitExceeded")

    # S27: batch 2 redelivers an older version of a PO line, out of order. Core must keep the newer one.
    pr, po, line = w.chain(GB, gb_sup[11], goods[3], q("30.000"), q("10.000000"), d("2026-09-01"))
    w.rows[(1, "sap_po_item")][-1]["changed_at"] = "20260930120000"
    rec(GB, d("2026-09-08"), line)
    inv = w.invoice(GB, gb_sup[11], d("2026-09-14"), [(line, q("30"), q("300.00"))])
    w.emit(
        2,
        "sap_po_item",
        {**w.rows[(1, "sap_po_item")][-1], "menge": Decimal("999.000"), "changed_at": "20260930100000"},
        "U",
    )
    w.expect(
        "S27",
        "Out-of-order older version ignored",
        "GB01",
        SAP,
        "PO_LINE",
        po.number,
        line.number,
        "StaleIgnored",
        detail="quantity=30.000",
    )
    w.expect_invoice("S27", "Invoice on the PO line, unaffected", inv, "Matched")

    # S28: a requisition that never converted is cancelled in the next change log.
    pr = w.requisition(GB, gb_req[2], gb_sup[12], goods[4], q("10.000"), q("10.000000"), d("2026-09-02"))
    w.emit(
        2,
        "sap_pr",
        {
            **[r for r in w.rows[(1, "sap_pr")] if r["banfn"] == pr.number][0],
            "changed_at": sap_ts(stamp(d("2026-10-01"))),
        },
        "D",
    )
    w.expect("S28", "Cancelled by a CDC delete", "GB01", SAP, "REQUISITION", pr.number, "", "Deleted")

    # S29: a second delivery arrives late, stamped before the batch 1 high watermark. Only the lookback catches it.
    pr, po, line = w.chain(DE, de_sup[13], goods[5], q("100.000"), q("10.000000"), d("2026-09-01"))
    rec(DE, d("2026-09-10"), line, q("60.000"), changed=datetime(2026, 9, 30, 18))
    inv = w.invoice(DE, de_sup[13], d("2026-09-20"), [(line, q("100"), q("1000.00"))])
    rec(DE, d("2026-09-15"), line, q("40.000"), changed=datetime(2026, 9, 30, 6), batch=2, cdc="I")
    w.expect_invoice(
        "S29", "Before the late delivery", inv, "Exception", "QuantityVariance", phase="batch_001"
    )
    w.expect_invoice("S29", "Late delivery inside the watermark lookback", inv, "Matched", phase="final")

    pr, po, line = w.chain(GB, gb_sup[13], goods[6], q("10.000"), q("10.000000"), d("2026-09-07"))
    inv = w.invoice(GB, gb_sup[13], d("2026-09-21"), [(line, q("10"), q("100.00"))])
    w.expect_invoice("S30", "Goods invoiced, nothing received yet", inv, "PendingReceipt")

    # S31: entirely new documents in the change log, dated after the cut-off.
    pr, po, line = w.chain(GB, gb_sup[14], goods[7], q("10.000"), q("10.000000"), d("2026-09-28"), batch=2)
    w.goods_receipt(GB, d("2026-10-01"), [(line, q("10.000"))], batch=2, cdc="I")
    inv = w.invoice(GB, gb_sup[14], d("2026-10-01"), [(line, q("10"), q("100.00"))], batch=2)
    w.expect_invoice("S31", "New documents arriving incrementally", inv, "Matched", phase="final")

    # S32: a genuine further invoice after a held duplicate. If the duplicate consumed quantity, this would overrun.
    pr, po, line = w.chain(GB, gb_sup[15], goods[8], q("100.000"), q("10.000000"), d("2026-06-01"))
    rec(GB, d("2026-06-09"), line)
    original = w.invoice(GB, gb_sup[15], d("2026-06-15"), [(line, q("50"), q("500.00"))], number="S32-0001")
    duplicate = w.invoice(GB, gb_sup[15], d("2026-06-20"), [(line, q("50"), q("500.00"))], number="S32/0001")
    second = w.invoice(GB, gb_sup[15], d("2026-06-25"), [(line, q("50"), q("500.00"))], number="S32-0002")
    w.expect_invoice("S32", "First instalment", original, "Matched")
    w.expect_invoice(
        "S32", "Duplicate of the first instalment, held", duplicate, "Exception", "DuplicateInvoice"
    )
    w.expect_invoice("S32", "Second instalment, unaffected by the held duplicate", second, "Matched")

    # S33: half invoiced plus a held duplicate of that half. The other half is still an accrual.
    pr, po, line = w.chain(DE, de_sup[14], goods[9], q("100.000"), q("10.000000"), d("2026-07-06"))
    rec(DE, d("2026-07-14"), line)
    original = w.invoice(DE, de_sup[14], d("2026-07-20"), [(line, q("50"), q("500.00"))], number="S33-0001")
    duplicate = w.invoice(DE, de_sup[14], d("2026-07-27"), [(line, q("50"), q("500.00"))], number="S33 0001")
    w.expect_invoice("S33", "Part invoice", original, "Matched")
    w.expect_invoice("S33", "Held duplicate of the part invoice", duplicate, "Exception", "DuplicateInvoice")
    w.expect(
        "S33",
        "A held duplicate does not reduce GRNI",
        "DE01",
        ORA,
        "PO_LINE",
        po.number,
        line.number,
        "InGRNI",
        "",
        detail="grni_quantity=50.000;grni_amount=500.00",
    )

    # S34: a newly onboarded supplier's first bank account is not a change, however recent.
    onboarded = gb_sup[16]
    w.rows[(1, "sap_supplier_bank")] = [
        {**r, "valid_from": sap_date(d("2026-04-01"))} if r["lifnr"] == onboarded.source_id else r
        for r in w.rows[(1, "sap_supplier_bank")]
    ]
    w.reserved.add(onboarded.source_id)
    pr, po, line = w.chain(GB, onboarded, goods[10], q("10.000"), q("10.000000"), d("2026-04-06"))
    rec(GB, d("2026-04-14"), line)
    inv = w.invoice(GB, onboarded, d("2026-04-20"), [(line, q("10"), q("100.00"))])
    w.expect_invoice("S34", "First bank account set up 19 days before the invoice", inv, "Matched")

    # S35: two requisitions that together breach a tier, until one is cancelled in the change log. The survivor is clean.
    keep = w.requisition(DE, de_req[2], de_sup[15], goods[11], q("100.000"), q("30.000000"), d("2026-09-14"))
    cancel = w.requisition(
        DE, de_req[2], de_sup[15], goods[11], q("100.000"), q("30.000000"), d("2026-09-16")
    )
    w.emit(
        2,
        "ora_requisitions",
        {
            **[r for r in w.rows[(1, "ora_requisitions")] if r["requisition_number"] == cancel.number][0],
            "last_update_date": ora_ts(stamp(d("2026-10-01"))),
        },
        "D",
    )
    for pr in (keep, cancel):
        w.expect(
            "S35",
            "Split group while both requisitions are live",
            "DE01",
            ORA,
            "REQUISITION",
            pr.number,
            "",
            "ControlBreach",
            "SplitRequisition",
            phase="batch_001",
        )
    w.expect(
        "S35",
        "Cancelling one member dissolves the split",
        "DE01",
        ORA,
        "REQUISITION",
        keep.number,
        "",
        "NoBreach",
        phase="final",
    )

    # S36: one minor unit over is rounding, and counts as an exact match rather than a tolerance match.
    pr, po, line = w.chain(GB, gb_sup[17], goods[12], q("10.000"), q("10.000000"), d("2026-07-13"))
    rec(GB, d("2026-07-21"), line)
    inv = w.invoice(GB, gb_sup[17], d("2026-07-27"), [(line, q("10"), q("100.01"))])
    w.expect_invoice("S36", "One penny over the PO amount", inv, "Matched")

    # A duplicate delivery of a row already landed: same content in a change log must change nothing.
    w.emit(2, "sap_goods_receipt", dict(w.rows[(1, "sap_goods_receipt")][0]), "U")


def build_background(world: World) -> None:
    """Clean documents up to the requisition target. Every invoice line here should match or match within tolerance."""
    rng = world.rng
    q = Decimal
    first, last = date(2026, 2, 2), date(2026, 8, 5)
    span = (last - first).days
    pools = {
        GB: [s for s in world.suppliers[SAP][8:] if s.source_id not in world.reserved],
        DE: [s for s in world.suppliers[ORA][8:] if s.source_id not in world.reserved],
    }

    def pick_requesters(company: Company, supplier: Supplier, day: date, count: int) -> list[Employee] | None:
        # Keep the split control honest: never the same requester and supplier twice inside its window by accident.
        people = list(world.employees[company.code]["requester"])
        rng.shuffle(people)
        free = [
            person
            for person in people
            if all(
                abs((day - other).days) > 10
                for other in world.requester_supplier_days.get((person.source_id, supplier.source_id), [])
            )
        ]
        return free[:count] if len(free) >= count else None

    def draft(company: Company, item: Item) -> tuple[Decimal, Decimal]:
        price = item.price
        band = rng.random()
        target = 2500 if band < 0.70 else 15000 if band < 0.95 else 60000
        qty = q(max(1, round(rng.uniform(0.2, 1.0) * target / float(price)))).quantize(q("0.001"))
        return qty, price.quantize(q("0.000001"))

    def variance(amount: Decimal) -> Decimal:
        roll = rng.random()
        if roll < 0.08:
            return money(amount * q("1.012"))
        if roll < 0.12:
            return money(amount * q("0.99"))
        if roll < 0.16:
            return amount + q("0.01")
        return amount

    while world.pr_count < PR_TARGET:
        company = GB if rng.random() < 0.5 else DE
        supplier = rng.choice(pools[company])
        start = first + timedelta(days=rng.randint(0, span))
        items = [rng.choice(world.items)]
        if rng.random() < 0.15 and world.pr_count + 2 <= PR_TARGET:
            items.append(
                rng.choice([i for i in world.items if i.kind == items[0].kind and i is not items[0]])
            )
        drafts = [(item, *draft(company, item)) for item in items]
        requesters = pick_requesters(company, supplier, start, len(drafts))
        if requesters is None:
            continue  # this supplier is busy on this date; draw again
        prs = [
            world.requisition(company, requester, supplier, item, qty, price, start)
            for requester, (item, qty, price) in zip(requesters, drafts, strict=True)
        ]
        if rng.random() < 0.08:
            continue  # requisition raised and approved, never converted
        po = world.purchase_order(company, supplier, start + timedelta(days=rng.randint(2, 5)))
        lines = [
            world.po_line(po, pr, item, qty, price)
            for pr, (item, qty, price) in zip(prs, drafts, strict=True)
        ]
        pattern = rng.random()
        day = po.day + timedelta(days=rng.randint(5, 12))
        if items[0].kind == "SERVICE":
            if pattern < 0.1:
                continue  # order placed, service not yet billed
            if pattern < 0.3:
                # Two instalments of the ordered quantity.
                first_part = [(line, (line.qty / 2).quantize(q("0.001"))) for line in lines]
                world.invoice(
                    company,
                    supplier,
                    day,
                    [(line, part, money(part * line.price)) for line, part in first_part],
                )
                world.invoice(
                    company,
                    supplier,
                    day + timedelta(days=14),
                    [
                        (line, line.qty - part, money((line.qty - part) * line.price))
                        for line, part in first_part
                    ],
                )
            else:
                world.invoice(
                    company,
                    supplier,
                    day,
                    [(line, line.qty, variance(money(line.qty * line.price))) for line in lines],
                )
            continue
        if pattern < 0.05:
            continue  # ordered, nothing delivered yet
        if pattern < 0.20:
            # Two deliveries, invoiced once in full after the second.
            part = [(line, (line.qty * q("0.6")).quantize(q("0.001"))) for line in lines]
            world.goods_receipt(company, day, part)
            later = day + timedelta(days=rng.randint(4, 10))
            world.goods_receipt(company, later, [(line, line.qty - p) for line, p in part])
            world.invoice(
                company,
                supplier,
                later + timedelta(days=rng.randint(2, 15)),
                [(line, line.qty, variance(money(line.qty * line.price))) for line in lines],
            )
        elif pattern < 0.32:
            # Two deliveries, each invoiced separately.
            part = [(line, (line.qty * q("0.5")).quantize(q("0.001"))) for line in lines]
            world.goods_receipt(company, day, part)
            world.invoice(
                company,
                supplier,
                day + timedelta(days=3),
                [(line, p, money(p * line.price)) for line, p in part],
            )
            later = day + timedelta(days=rng.randint(8, 14))
            world.goods_receipt(company, later, [(line, line.qty - p) for line, p in part])
            world.invoice(
                company,
                supplier,
                later + timedelta(days=3),
                [(line, line.qty - p, money((line.qty - p) * line.price)) for line, p in part],
            )
        elif pattern < 0.42:
            world.goods_receipt(
                company, day, [(line, line.qty) for line in lines]
            )  # received, not invoiced: GRNI
        else:
            world.goods_receipt(company, day, [(line, line.qty) for line in lines])
            world.invoice(
                company,
                supplier,
                day + timedelta(days=rng.randint(2, 15)),
                [(line, line.qty, variance(money(line.qty * line.price))) for line in lines],
            )


def build_documents(world: World, policy: dict) -> None:
    lines = [
        f"# {policy['title']}",
        "",
        f"Version {policy['policy_version']}, effective {policy['valid_from']}. Fictional and illustrative.",
        "",
    ]
    lines += ["## Tolerances, windows and approval tiers", ""]
    for code, parameter in policy["parameters"].items():
        lines.append(f"- **{code}** ({parameter['value']} {parameter['unit']}): {parameter['rule']}")
    for heading, key in (
        ("Segregation of duties", "segregation_of_duties"),
        ("Retrospective purchase orders", "retrospective_po"),
        ("Goods received not invoiced", "grni"),
    ):
        lines += ["", f"## {heading}", "", policy[key]]
    world.documents["policy_p2p.md"] = "\n".join(lines) + "\n"
    for contract in world.contracts:
        supplier = contract["supplier"]
        world.documents[f"contract_{contract['id']}_{contract['start'].isoformat()}.md"] = (
            f"# Price agreement {contract['id']} (synthetic)\n\n"
            f"Between {contract['company'].name} and {supplier.name} (supplier reference {supplier.source_id}).\n\n"
            f"Valid from {contract['start'].isoformat()} to {contract['end'].isoformat()}.\n\n"
            f"## Clause 3: Pricing\n\nItem {contract['item'].item_id} ({contract['item'].description}) at "
            f"{contract['company'].currency} {contract['price']:.2f} per {contract['item'].uom}.\n\n"
            f"## Clause 4: Invoice tolerance\n\nAn invoiced line may exceed the agreed amount by up to {contract['tolerance']:.1f} percent "
            "without further approval. Above this, the invoice is held pending a corrected invoice or credit note.\n"
        )
    world.documents["email_S17_receipt_query.md"] = (
        "# Supplier email (synthetic)\n\nDate: 28 September 2026\nSubject: Unpaid invoice\n\n"
        "Our delivery note shows 100 units delivered on 18 August, but you have queried only 80. Please check your receipt.\n"
    )


def _render(value: object) -> str:
    return "" if value is None else str(value)


def _write_batch(world: World, batch: int, out: Path, extracted_at: str) -> None:
    batch_id = f"batch_{batch:03d}"
    batch_dir = out / batch_id
    files = []
    for (row_batch, table), rows in sorted(world.rows.items()):
        if row_batch != batch or not rows:
            continue
        columns = COLUMNS[table].split()
        if batch > 1:
            columns = ["cdc_op", *columns]
        folder = {"sap": "sap", "ora": "oracle", "group": "group"}[table.split("_")[0]]
        system = {"sap": SAP, "ora": ORA, "group": "GROUP"}[table.split("_")[0]]
        delimiter = ";" if folder == "sap" else ","
        path = batch_dir / folder / f"{table}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, delimiter=delimiter, lineterminator="\n")
            writer.writerow(columns)
            for row in rows:
                writer.writerow([_render(row.get(column)) for column in columns])
        files.append(
            {
                "table": table,
                "path": f"{folder}/{table}.csv",
                "source_system": system,
                "delimiter": delimiter,
                "row_count": len(rows),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "batch_id": batch_id,
        "extracted_at": extracted_at,
        "mode": "full" if batch == 1 else "change_log",
        "files": files,
    }
    (batch_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def generate(out: Path, seed: int = DEFAULT_SEED, samples_dir: Path | None = None) -> dict:
    policy = yaml.safe_load((REFERENCE_DIR / "p2p_policy.yaml").read_text(encoding="utf-8"))
    world = World(seed)
    build_reference(world, policy)
    pairs = build_suppliers(world)
    build_scenarios(world, pairs)
    build_background(world)
    build_documents(world, policy)

    out.mkdir(parents=True, exist_ok=True)
    _write_batch(world, 1, out, BATCH_1_EXTRACTED)
    _write_batch(world, 2, out, BATCH_2_EXTRACTED)

    documents = out / "documents"
    documents.mkdir(exist_ok=True)
    for name, text in sorted(world.documents.items()):
        (documents / name).write_text(text, encoding="utf-8", newline="\n")

    fields = [
        "scenario_id",
        "description",
        "company_code",
        "source_system",
        "document_type",
        "document_number",
        "line_number",
        "expected_status",
        "expected_reason_codes",
        "phase",
        "detail",
    ]
    targets = [out / "expected_outcomes.csv"]
    if samples_dir is not None:
        samples_dir.mkdir(parents=True, exist_ok=True)
        targets.append(samples_dir / "expected_outcomes.csv")
    for target in targets:
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(world.expected)

    return {
        "seed": seed,
        "suppliers": sum(len(v) for v in world.suppliers.values()),
        "requisitions": world.pr_count,
        "expected_rows": len(world.expected),
        "documents": len(world.documents),
    }
