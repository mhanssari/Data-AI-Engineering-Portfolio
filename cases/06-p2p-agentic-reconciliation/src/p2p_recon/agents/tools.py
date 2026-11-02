"""The only way agents reach data. Every tool checks the acting user's companies and logs its call to agent.tool_call.

Read-only by construction: the connection runs as the p2p_agent role, which can select from core, mdm, recon and docs
and insert only into the agent trail. Neo4j sessions are opened in read mode. Matching is read from the current results
rather than re-run, because recon.run_matching writes a new snapshot and an agent may not write to recon.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from neo4j import Driver

from p2p_recon import retrieval
from p2p_recon.retrieval import ActingUser

CONTROL_REASONS = (
    "ApprovalLimitExceeded",
    "SegregationOfDuties",
    "MissingApproval",
    "SplitRequisition",
    "RetrospectivePO",
    "MissingRequisition",
    "BankDetailChange",
    "DuplicateInvoice",
)
WITHHELD = "withheld: held by another company"


class AccessDenied(Exception):
    """The acting user may not see this invoice. It looks the same as an invoice that does not exist."""


@dataclass(frozen=True)
class LineKey:
    source_system: str
    invoice_id: str
    line_number: int

    @property
    def invoice(self) -> tuple[str, str]:
        return (self.source_system, self.invoice_id)

    def as_dict(self) -> dict:
        return {
            "source_system": self.source_system,
            "invoice_id": self.invoice_id,
            "line_number": self.line_number,
        }


def row_citation(table: str, **key: object) -> str:
    return f"row:{table}|{json.dumps(key, sort_keys=True, default=str)}"


class ToolBox:
    def __init__(self, conn: psycopg.Connection, db: Driver, user: ActingUser):
        self.conn = conn
        self.db = db
        self.user = user
        self.step_id: int | None = None

    def _call(self, name: str, arguments: dict, body: Callable[[], object]) -> object:
        started = time.perf_counter()
        status, result = "ok", None
        try:
            result = body()
            return result
        except AccessDenied as exc:
            status, result = "denied", {"error": str(exc)}
            raise
        except Exception as exc:
            status, result = "error", {"error": f"{type(exc).__name__}: {exc}"}
            raise
        finally:
            self.conn.execute(
                "insert into agent.tool_call (step_id, tool_name, arguments, result, status, latency_ms) "
                "values (%s, %s, %s, %s, %s, %s)",
                (
                    self.step_id,
                    name,
                    json.dumps(arguments, default=str),
                    json.dumps(result, default=str),
                    status,
                    round((time.perf_counter() - started) * 1000, 1),
                ),
            )

    def read_queue_item(self, key: LineKey) -> dict:
        """Supervisor: status and reason codes for a line, or a refusal."""

        def body() -> dict:
            row = self.conn.execute(
                """
                select m.run_id, m.company_code, m.status_code, m.document_type,
                       array(select e.reason_code from recon.current_exception e
                             where e.source_system = m.source_system and e.invoice_id = m.invoice_id
                               and e.line_number = m.line_number order by 1) as reasons
                from recon.current_match_result m
                where m.source_system = %s and m.invoice_id = %s and m.line_number = %s and m.company_code = any(%s)
                """,
                (key.source_system, key.invoice_id, key.line_number, self.user.company_codes),
            ).fetchone()
            if row is None:
                raise AccessDenied(
                    f"No invoice line {key.invoice_id}/{key.line_number} visible to {self.user.user_name}"
                )
            return row

        return self._call("read_queue_item", key.as_dict(), body)

    def _redact(self, exception: dict) -> dict:
        """A duplicate's original can sit in another company. Its existence matters to this user; its identity does not."""
        evidence = dict(exception["evidence"])
        original = evidence.get("original_invoice")
        if original is not None:
            visible = self.conn.execute(
                "select 1 from recon.current_match_result where company_code = any(%s) and (invoice_id = %s or source_system || ':' || invoice_id = %s) limit 1",
                (self.user.company_codes, str(original), str(original)),
            ).fetchone()
            if not visible:
                evidence["original_invoice"] = WITHHELD
        return {**exception, "evidence": evidence}

    def check_controls(self, key: LineKey) -> dict:
        """Controls agent: control-type exceptions on this line and the requisition breaches behind them."""

        def body() -> dict:
            exceptions = self.conn.execute(
                """
                select e.run_id, e.reason_code, e.evidence
                from recon.current_exception e
                where e.source_system = %s and e.invoice_id = %s and e.line_number = %s and e.reason_code = any(%s)
                order by e.reason_code
                """,
                (key.source_system, key.invoice_id, key.line_number, list(CONTROL_REASONS)),
            ).fetchall()
            exceptions = [self._redact(e) for e in exceptions]
            requisitions = sorted(
                {e["evidence"]["pr_number"] for e in exceptions if "pr_number" in e["evidence"]}
            )
            breaches = self.conn.execute(
                """
                select b.run_id, b.source_system, b.pr_number, b.reason_code, b.evidence
                from recon.current_control_breach b
                where b.source_system = %s and b.pr_number = any(%s) and b.company_code = any(%s)
                order by b.pr_number, b.reason_code
                """,
                (key.source_system, requisitions, self.user.company_codes),
            ).fetchall()
            return {"exceptions": exceptions, "breaches": breaches}

        return self._call("check_controls", key.as_dict(), body)

    def get_match_result(self, key: LineKey) -> dict:
        """Matching agent: the SQL result for the line, with every exception. Amounts come from here and nowhere else."""

        def body() -> dict:
            match = self.conn.execute(
                """
                select m.run_id, m.company_code, m.currency_code, m.document_type, m.match_type, m.status_code, m.net_amount,
                       m.expected_amount, m.amount_variance, m.tolerance_pct, m.tolerance_source, m.cumulative_invoiced,
                       m.allowed_quantity
                from recon.current_match_result m
                where m.source_system = %s and m.invoice_id = %s and m.line_number = %s and m.company_code = any(%s)
                """,
                (key.source_system, key.invoice_id, key.line_number, self.user.company_codes),
            ).fetchone()
            if match is None:
                raise AccessDenied(
                    f"No invoice line {key.invoice_id}/{key.line_number} visible to {self.user.user_name}"
                )
            exceptions = self.conn.execute(
                """
                select e.run_id, e.reason_code, e.evidence from recon.current_exception e
                where e.source_system = %s and e.invoice_id = %s and e.line_number = %s order by e.reason_code
                """,
                (key.source_system, key.invoice_id, key.line_number),
            ).fetchall()
            return {"match": match, "exceptions": [self._redact(e) for e in exceptions]}

        return self._call("get_match_result", key.as_dict(), body)

    def get_contract_terms(self, key: LineKey) -> list[dict]:
        """Matching agent: the contract version governing this line on the invoice date, if there is one."""

        def body() -> list[dict]:
            return self.conn.execute(
                """
                select c.contract_id, c.valid_from, c.valid_to, c.item_id, c.agreed_price, c.currency_code, c.price_tolerance_pct
                from core.invoice_line l
                join core.supplier_invoice i on i.source_system = l.source_system and i.invoice_id = l.invoice_id
                join core.po_line p on p.source_system = l.source_system and p.po_number = l.po_number
                     and p.line_number = l.po_line_number
                join core.contract c on c.contract_id = p.contract_id and i.invoice_date between c.valid_from and c.valid_to
                where l.source_system = %s and l.invoice_id = %s and l.line_number = %s and c.company_code = any(%s)
                """,
                (key.source_system, key.invoice_id, key.line_number, self.user.company_codes),
            ).fetchall()

        return self._call("get_contract_terms", key.as_dict(), body)

    def get_document_chain(self, key: LineKey) -> dict | None:
        """Investigator: the graph neighbourhood of the invoice, filtered to what the user may see."""
        return self._call(
            "get_document_chain",
            key.as_dict(),
            lambda: retrieval.invoice_neighbourhood(self.db, self.user, *key.invoice),
        )

    def search_documents(self, query: str, key: LineKey, rules: int = 2, case: int = 3) -> list[dict]:
        """Investigator: the rules that apply, then what is on file about this case, permission-filtered before ranking.

        Two searches, because they answer different questions. In one ranked list the graph's links to a supplier's
        contract and emails push the governing policy rule out of the top results, although an explanation needs both.
        """

        def body() -> list[dict]:
            found = retrieval.search(
                self.conn, self.user, query, "hybrid", rules, within=retrieval.rule_document_ids(self.conn)
            )
            seen = {hit.chunk_id for hit in found}
            on_file = retrieval.search(
                self.conn, self.user, query, "hybrid_graph", rules + case, self.db, key.invoice
            )
            hits = found + [hit for hit in on_file if hit.chunk_id not in seen][:case]
            return [
                {
                    "citation": hit.citation,
                    "doc_type": hit.doc_type,
                    "context_header": hit.context_header,
                    "content": hit.content,
                }
                for hit in hits
            ]

        return self._call(
            "search_documents", {"query": query, **key.as_dict(), "rules": rules, "case": case}, body
        )
