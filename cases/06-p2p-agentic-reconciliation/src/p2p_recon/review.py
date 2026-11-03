"""What a finance user does with a proposal: read it, check its evidence, decide. Used by the CLI and the Streamlit page.

Every function takes a connection already acting as that person's role. Row-level security decides what comes back, and the
policy on agent.human_decision decides who may record what, so these functions hold no permission logic of their own that
an interface could skip. Recording a decision posts nothing and pays nothing.
"""

import psycopg
from neo4j import Driver

from p2p_recon.agents.tools import LineKey
from p2p_recon.lineage import fetch_cited_row
from p2p_recon.retrieval import ActingUser, invoice_neighbourhood

DECISIONS = ("ACCEPT", "OVERRIDE", "QUERY")
CONTROL_REASONS = {
    "ApprovalLimitExceeded",
    "SegregationOfDuties",
    "MissingApproval",
    "SplitRequisition",
    "RetrospectivePO",
    "MissingRequisition",
}


class DecisionRefused(Exception):
    """The database, or a rule on what a decision must contain, refused it."""


def queue(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        """
        select proposal_id, company_code, source_system, invoice_id, line_number, sql_status, sql_reasons,
               proposed_action, explanation, guard_flags
        from agent.review_queue order by proposal_id
        """
    ).fetchall()


def citation_details(conn: psycopg.Connection, proposal_id: int) -> list[dict]:
    """Each citation with what it says, as this user can see it. A row or chunk they cannot see says so, rather than vanishing."""
    details = []
    for citation in conn.execute(
        "select kind, chunk_id, cited_table, cited_key from agent.citation where proposal_id = %s order by citation_id",
        (proposal_id,),
    ).fetchall():
        if citation["kind"] == "chunk":
            chunk = conn.execute(
                "select c.context_header, c.content from docs.chunk c where c.chunk_id = %s",
                (citation["chunk_id"],),
            ).fetchone()
            details.append(
                {
                    "kind": "document",
                    "reference": citation["chunk_id"],
                    "header": chunk["context_header"] if chunk else None,
                    "content": chunk["content"] if chunk else None,
                }
            )
        else:
            details.append(
                {
                    "kind": "row",
                    "reference": citation["cited_table"],
                    "key": citation["cited_key"],
                    "row": fetch_cited_row(conn, citation),
                }
            )
    return details


def proposal_detail(conn: psycopg.Connection, proposal_id: int) -> dict | None:
    """None when the proposal does not exist or belongs to a company this user cannot see."""
    proposal = conn.execute("select * from agent.proposal where proposal_id = %s", (proposal_id,)).fetchone()
    if proposal is None:
        return None
    key = (proposal["source_system"], proposal["invoice_id"], proposal["line_number"])
    return {
        "proposal": proposal,
        "match": conn.execute(
            """
            select status_code, match_type, currency_code, net_amount, expected_amount, amount_variance, tolerance_pct,
                   tolerance_source, cumulative_invoiced, allowed_quantity
            from recon.current_match_result where source_system = %s and invoice_id = %s and line_number = %s
            """,
            key,
        ).fetchone(),
        "citations": citation_details(conn, proposal_id),
        "decisions": conn.execute(
            "select decision, decided_by, note, decided_at from agent.human_decision where proposal_id = %s order by decision_id",
            (proposal_id,),
        ).fetchall(),
    }


def graph_path_dot(conn_user: ActingUser, db: Driver, key: LineKey) -> str | None:
    """The invoice's neighbourhood as Graphviz text. It uses the same permission-filtered query as the agents."""
    around = invoice_neighbourhood(db, conn_user, key.source_system, key.invoice_id)
    if around is None:
        return None

    def label(text: object) -> str:
        return str(text).replace("\\", "\\\\").replace('"', '\\"')

    lines = ["digraph path {", "  rankdir=LR;", '  node [shape=box, style="rounded", fontsize=11];']
    invoice = f'"Invoice\\n{label(around["invoice_number"])}"'
    supplier = f'"Supplier\\n{label(around["supplier_name"])}"'
    lines.append(f"  {invoice} -> {supplier} [label=issued_by];")
    if around["golden_supplier_id"]:
        lines.append(
            f'  {supplier} -> "Golden supplier\\n{label(around["golden_supplier_id"])}" [label=resolves_to];'
        )
    if around["hidden_siblings"]:
        lines.append(
            f'  "Golden supplier\\n{label(around["golden_supplier_id"])}" -> "{around["hidden_siblings"]} record(s)\\nin another company\\n(not shown)" [style=dashed];'
        )
    for sibling in around["visible_siblings"]:
        lines.append(
            f'  "Golden supplier\\n{label(around["golden_supplier_id"])}" -> "Same supplier\\n{label(sibling)}";'
        )
    for number in around["contract_ids"]:
        lines.append(
            f'  "PO\\n{label(around["po_numbers"][0])}" -> "Contract\\n{label(number)}" [label=governed_by];'
        )
    for number in around["po_numbers"]:
        lines.append(f'  {invoice} -> "PO\\n{label(number)}" [label=invoiced_against];')
        for grn in around["grn_numbers"]:
            lines.append(f'  "Receipt\\n{label(grn)}" -> "PO\\n{label(number)}" [label=received_against];')
        for requisition in around["pr_numbers"]:
            lines.append(
                f'  "Requisition\\n{label(requisition)}" -> "PO\\n{label(number)}" [label=converted_to];'
            )
    for requisition in around["pr_numbers"]:
        for approver in around["approvers"]:
            lines.append(
                f'  "Requisition\\n{label(requisition)}" -> "Approver\\n{label(approver)}" [label=approved_by];'
            )
        for requester in around["requesters"]:
            lines.append(
                f'  "Requisition\\n{label(requisition)}" -> "Requester\\n{label(requester)}" [label=requested_by];'
            )
    lines.append("}")
    return "\n".join(dict.fromkeys(lines))


def needs_controller(proposal: dict) -> bool:
    """For the interface's wording only. The policy on agent.human_decision is what enforces this."""
    return bool(CONTROL_REASONS & set(proposal["sql_reasons"]))


def decide(conn: psycopg.Connection, proposal_id: int, decision: str, note: str | None = None) -> int:
    """Records the decision as the role in use. The database refuses anything this role may not do."""
    if decision not in DECISIONS:
        raise DecisionRefused(f"Decision must be one of {', '.join(DECISIONS)}")
    if decision == "OVERRIDE" and not (note or "").strip():
        raise DecisionRefused("An override must say why")
    try:
        return conn.execute(
            """
            insert into agent.human_decision (proposal_id, decision, decided_by, note)
            values (%s, %s, current_user, %s) returning decision_id
            """,
            (proposal_id, decision, note),
        ).fetchone()["decision_id"]
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ForeignKeyViolation) as exc:
        raise DecisionRefused(f"Not permitted: {exc.diag.message_primary}") from exc
