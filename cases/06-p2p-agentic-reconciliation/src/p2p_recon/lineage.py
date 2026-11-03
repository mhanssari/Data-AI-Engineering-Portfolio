"""Trace one agent proposal back to what it rests on: tool calls, cited rows and chunks, core rows, raw rows and source files.

Row-level lineage comes from core.source_raw_id; dataset-level lineage comes from ops.lineage_edge. A trace reads raw and
ops, so it runs on the pipeline's own login and is an administrator's or auditor's tool. It reports where a raw row came
from and never its values, so bank numbers and other source fields cannot leak through it.
"""

import json

import psycopg
from psycopg import sql

from p2p_recon.db import connect

# Tables a row citation may point at, with the columns that form each key.
CITABLE = {
    "recon.match_result": ("run_id", "source_system", "invoice_id", "line_number"),
    "recon.exception": ("run_id", "source_system", "invoice_id", "line_number", "reason_code"),
    "recon.control_breach": ("run_id", "source_system", "pr_number", "reason_code"),
    "core.contract": ("contract_id", "valid_from"),
}

# The document chain behind an invoice line, as (table, predicate using the line's own columns). OPTIONAL by construction:
# a chain with a gap simply has fewer rows, which is often the point of the exception.
CHAIN = [
    ("core.invoice_line", "source_system = %(s)s and invoice_id = %(i)s and line_number = %(n)s"),
    ("core.supplier_invoice", "source_system = %(s)s and invoice_id = %(i)s"),
    (
        "core.supplier",
        "(source_system, source_supplier_id) in (select source_system, source_supplier_id from core.supplier_invoice where source_system = %(s)s and invoice_id = %(i)s)",
    ),
    (
        "core.po_line",
        "(source_system, po_number, line_number) in (select source_system, po_number, po_line_number from core.invoice_line where source_system = %(s)s and invoice_id = %(i)s and line_number = %(n)s)",
    ),
    (
        "core.purchase_order",
        "(source_system, po_number) in (select source_system, po_number from core.invoice_line where source_system = %(s)s and invoice_id = %(i)s and line_number = %(n)s)",
    ),
    (
        "core.grn_line",
        "(source_system, po_number, po_line_number) in (select source_system, po_number, po_line_number from core.invoice_line where source_system = %(s)s and invoice_id = %(i)s and line_number = %(n)s)",
    ),
    (
        "core.purchase_requisition",
        "(source_system, pr_number) in (select p.source_system, p.pr_number from core.po_line p join core.invoice_line l on l.source_system = p.source_system and l.po_number = p.po_number and l.po_line_number = p.line_number where l.source_system = %(s)s and l.invoice_id = %(i)s and l.line_number = %(n)s)",
    ),
    (
        "core.approval",
        "(source_system, pr_number) in (select p.source_system, p.pr_number from core.po_line p join core.invoice_line l on l.source_system = p.source_system and l.po_number = p.po_number and l.po_line_number = p.line_number where l.source_system = %(s)s and l.invoice_id = %(i)s and l.line_number = %(n)s)",
    ),
]


def fetch_cited_row(conn: psycopg.Connection, citation: dict) -> dict | None:
    """The row a citation names, as the connection's role can see it. None if it is absent, hidden or not citable."""
    columns = CITABLE.get(citation["cited_table"])
    key = citation["cited_key"]
    if columns is None or set(key) != set(columns):
        return None
    schema, table = citation["cited_table"].split(".")
    query = sql.SQL("select * from {} where {}").format(
        sql.Identifier(schema, table),
        sql.SQL(" and ").join(sql.SQL("{}::text = %s").format(sql.Identifier(column)) for column in columns),
    )
    return conn.execute(query, [str(key[column]) for column in columns]).fetchone()


def _raw_origins(conn: psycopg.Connection, raw_ids: list[int]) -> dict[int, dict]:
    """raw_id is unique across the raw schema, so each row is found by looking in every raw table's primary key."""
    tables = [
        row["table_name"]
        for row in conn.execute(
            "select table_name from information_schema.tables where table_schema = 'raw' and table_name <> 'meta_template'"
        )
    ]
    origins: dict[int, dict] = {}
    for table in tables:
        query = sql.SQL(
            """
            select r.raw_id, r.source_system, r.batch_id, r.source_file, r.file_row, r.extracted_at, r.cdc_op, f.checksum
            from {} r
            left join ops.ingest_file f on f.source_system = r.source_system and f.file_name = r.batch_id || '/' || r.source_file and f.run_id = r.run_id
            where r.raw_id = any(%s)
            """
        ).format(sql.Identifier("raw", table))
        for row in conn.execute(query, (raw_ids,)):
            origins[row["raw_id"]] = {"raw_table": f"raw.{table}", **row}
    return origins


def dataset_path(conn: psycopg.Connection, targets: list[str]) -> list[dict]:
    """Every dataset edge upstream of the targets, from ops.lineage_edge, nearest first."""
    edges = conn.execute(
        """
        select source_dataset, target_dataset, max(run_id) as latest_run_id
        from ops.lineage_edge group by 1, 2
        """
    ).fetchall()
    by_target: dict[str, list[dict]] = {}
    for edge in edges:
        by_target.setdefault(edge["target_dataset"], []).append(edge)
    path, seen, frontier = [], set(), list(targets)
    while frontier:
        target = frontier.pop(0)
        for edge in sorted(by_target.get(target, []), key=lambda e: e["source_dataset"]):
            pair = (edge["source_dataset"], edge["target_dataset"])
            if pair not in seen:
                seen.add(pair)
                path.append(edge)
                frontier.append(edge["source_dataset"])
    return path


def trace_proposal(proposal_id: int, conn: psycopg.Connection | None = None) -> dict:
    own = conn is None
    conn = conn or connect()
    try:
        proposal = conn.execute(
            "select * from agent.proposal where proposal_id = %s", (proposal_id,)
        ).fetchone()
        if proposal is None:
            raise ValueError(f"No proposal {proposal_id}")
        line = {"s": proposal["source_system"], "i": proposal["invoice_id"], "n": proposal["line_number"]}
        run = conn.execute(
            "select * from agent.run where agent_run_id = %s", (proposal["agent_run_id"],)
        ).fetchone()
        steps = conn.execute(
            """
            select step_id, agent_name, step_index, input_tokens, output_tokens, tokens_estimated, cost_usd, latency_ms, error
            from agent.step
            where agent_run_id = %s and source_system = %s and invoice_id = %s and line_number = %s
            order by step_id
            """,
            (
                proposal["agent_run_id"],
                proposal["source_system"],
                proposal["invoice_id"],
                proposal["line_number"],
            ),
        ).fetchall()
        calls = conn.execute(
            "select step_id, tool_name, arguments, status, latency_ms from agent.tool_call where step_id = any(%s) order by tool_call_id",
            ([s["step_id"] for s in steps],),
        ).fetchall()

        cited = conn.execute(
            "select * from agent.citation where proposal_id = %s order by citation_id", (proposal_id,)
        ).fetchall()
        rows, chunks = [], []
        for citation in cited:
            if citation["kind"] == "row":
                rows.append(
                    {
                        "table": citation["cited_table"],
                        "key": citation["cited_key"],
                        "row": fetch_cited_row(conn, citation),
                    }
                )
            else:
                chunk = conn.execute(
                    """
                    select c.chunk_id, c.section, c.content, c.company_code, d.document_id, d.file_name, d.doc_type, d.content_sha256
                    from docs.chunk c join docs.document d using (document_id) where c.chunk_id = %s
                    """,
                    (citation["chunk_id"],),
                ).fetchone()
                chunks.append(chunk or {"chunk_id": citation["chunk_id"], "missing": True})

        chain = []
        for table, predicate in CHAIN:
            schema, name = table.split(".")
            query = sql.SQL("select * from {} where " + predicate).format(sql.Identifier(schema, name))
            chain += [
                {"table": table, "source_raw_id": row["source_raw_id"], "row": row}
                for row in conn.execute(query, line)
            ]
        origins = _raw_origins(conn, [item["source_raw_id"] for item in chain])
        for item in chain:
            item["origin"] = origins.get(item["source_raw_id"])

        return {
            "proposal": proposal,
            "run": run,
            "steps": steps,
            "tool_calls": calls,
            "cited_rows": rows,
            "cited_chunks": chunks,
            "source_chain": chain,
            "decisions": conn.execute(
                "select * from agent.human_decision where proposal_id = %s order by decision_id",
                (proposal_id,),
            ).fetchall(),
            "audit": conn.execute(
                "select occurred_at, event, role_in_use, on_behalf_of, detail from agent.audit_log where proposal_id = %s order by audit_id",
                (proposal_id,),
            ).fetchall(),
            "datasets": dataset_path(conn, ["agent.proposal"]),
        }
    finally:
        if own:
            conn.close()


def render_markdown(trace: dict) -> str:
    p = trace["proposal"]
    lines = [
        f"# Trace of proposal {p['proposal_id']}",
        "",
        f"**Invoice line:** {p['source_system']} {p['invoice_id']} line {p['line_number']}, company {p['company_code']}.",
        f"**SQL says:** {p['sql_status']}, reasons {', '.join(p['sql_reasons']) or 'none'}.",
        f"**Agent run:** {trace['run']['agent_run_id']}, provider `{trace['run']['provider']}`, on behalf of `{trace['run']['acting_user']}`, reading recon run {trace['run']['recon_run_id']}.",
        f"**Drafted:** {p['drafted_action']}. **Stored:** {p['proposed_action']}. **Guard flags:** {', '.join(p['guard_flags']) or 'none'}.",
        "",
        f"> {p['explanation']}",
        "",
        "## 1. What the agents did",
        "",
        "| Step | Agent | Tool calls | Latency ms |",
        "| --- | --- | --- | --- |",
    ]
    for step in trace["steps"]:
        tools = [
            f"{c['tool_name']} ({c['status']})"
            for c in trace["tool_calls"]
            if c["step_id"] == step["step_id"]
        ]
        lines.append(
            f"| {step['step_id']} | {step['agent_name']} | {', '.join(tools) or '–'} | {step['latency_ms']} |"
        )
    lines += ["", "## 2. Rows cited", "", "| Table | Key |", "| --- | --- |"]
    for row in trace["cited_rows"]:
        status = "" if row["row"] else " **(does not resolve)**"
        lines.append(f"| {row['table']} | `{json.dumps(row['key'], sort_keys=True)}`{status} |")
    lines += [
        "",
        "## 3. Documents cited",
        "",
        "| Chunk | File | SHA-256 of the file | Text |",
        "| --- | --- | --- | --- |",
    ]
    for chunk in trace["cited_chunks"]:
        if chunk.get("missing"):
            lines.append(f"| {chunk['chunk_id']} | **does not resolve** | | |")
        else:
            text = chunk["content"].replace("\n", " ").replace("|", "\\|")
            lines.append(
                f"| {chunk['chunk_id']} | {chunk['file_name']} | `{chunk['content_sha256'][:16]}…` | {text} |"
            )
    lines += [
        "",
        "## 4. Source rows behind the invoice line",
        "",
        "From each `core` row through `source_raw_id` to the delivered file. Where a row was corrected, this is the version in force.",
        "",
        "| Core table | Raw row | Source file | File row | Batch | CDC | File checksum |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in trace["source_chain"]:
        origin = item["origin"]
        if origin is None:
            lines.append(f"| {item['table']} | {item['source_raw_id']} | **not found** | | | | |")
        else:
            lines.append(
                f"| {item['table']} | {origin['raw_table']} #{origin['raw_id']} | {origin['source_file']} | {origin['file_row']} | {origin['batch_id']} | {origin['cdc_op'] or '–'} | `{(origin['checksum'] or '')[:16]}…` |"
            )
    lines += [
        "",
        "## 5. Dataset lineage",
        "",
        "From `ops.lineage_edge`, newest run of each edge, nearest to the proposal first.",
        "",
        "| From | To | Latest run |",
        "| --- | --- | --- |",
    ]
    for edge in trace["datasets"]:
        lines.append(f"| {edge['source_dataset']} | {edge['target_dataset']} | {edge['latest_run_id']} |")
    lines += [
        "",
        "## 6. Audit log and decisions",
        "",
        "| When | Event | Role in use | On behalf of | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for event in trace["audit"]:
        detail = json.dumps(event["detail"], sort_keys=True, default=str).replace("|", "\\|")
        lines.append(
            f"| {event['occurred_at']:%Y-%m-%d %H:%M:%S} | {event['event']} | {event['role_in_use']} | {event['on_behalf_of']} | `{detail}` |"
        )
    return "\n".join(lines) + "\n"
