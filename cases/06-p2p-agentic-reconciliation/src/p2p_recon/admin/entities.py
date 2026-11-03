"""Entities and how they relate: search in PostgreSQL, neighbourhoods and chain gaps in Neo4j, supplier resolution in mdm.

Neo4j Community Edition has no role-based access, so every graph read here is scoped in the query: nodes from a source
system, or legal entities, outside the reviewer's companies are left out, exactly as the agents' graph tool does. Sessions
are opened read-only, so a query here cannot change the graph whatever it contains.
"""

from dataclasses import dataclass

import psycopg
from neo4j import READ_ACCESS, Driver

from p2p_recon.admin.masking import mask
from p2p_recon.graph import QUERIES_DIR
from p2p_recon.ontology import load_mapping

MAX_DEPTH = 3
MAX_PATHS = 300
# What can be searched for: the graph label and the columns matched against the search text.
SEARCHABLE = {
    "invoice": (
        "SupplierInvoice",
        "core.supplier_invoice",
        ["invoice_id", "supplier_invoice_number"],
        "supplier_invoice_number",
    ),
    "purchase order": ("PurchaseOrder", "core.purchase_order", ["po_number"], "po_number"),
    "requisition": ("PurchaseRequisition", "core.purchase_requisition", ["pr_number"], "pr_number"),
    "supplier": ("Supplier", "core.supplier", ["source_supplier_id", "supplier_name"], "supplier_name"),
    "golden supplier": (
        "GoldenSupplier",
        "mdm.supplier_golden",
        ["supplier_gid", "golden_name"],
        "golden_name",
    ),
}


@dataclass(frozen=True)
class Scope:
    company_codes: list[str]
    source_systems: list[str]


def reviewer_scope(conn: psycopg.Connection) -> Scope:
    """The reviewer's companies from agent.reviewer, and the source systems those companies run on."""
    row = conn.execute(
        """
        select r.company_codes,
               array(select le.source_system from core.legal_entity le where le.company_code = any(r.company_codes)) as systems
        from agent.reviewer r where r.role_name = current_user
        """
    ).fetchone()
    return Scope(row["company_codes"], row["systems"]) if row else Scope([], [])


def labels() -> list[str]:
    return list(load_mapping()["classes"])


def relationship_types() -> list[str]:
    return sorted({rel["type"] for rel in load_mapping()["relationships"]})


def _key_sql(columns: list[str]) -> str:
    return "concat_ws('|', " + ", ".join(f"{c}::text" for c in columns) + ")"


def search(conn: psycopg.Connection, kind: str, text: str, limit: int = 25) -> list[dict]:
    """Matches by number, id or name, as the reviewer can see them. The key is the one the graph projection uses."""
    label, table, columns, shown = SEARCHABLE[kind]
    key = load_mapping()["classes"][label]["key"]
    match = " or ".join(f"{c}::text ilike %(text)s" for c in columns)
    deleted = "" if table.startswith("mdm.") else " and not is_deleted"
    # Table and column names are constants above; only the search text is a parameter.
    return conn.execute(
        f"""
        select %(label)s as label, {_key_sql(key)} as key, {shown}::text as name
        from {table} where ({match}){deleted}
        order by 2 limit %(limit)s
        """,
        {"label": label, "text": f"%{text.strip()}%", "limit": limit},
    ).fetchall()


def _node_name(properties: dict, key: str) -> str:
    for name in ("documentNumber", "partyName", "description", "companyCode"):
        if properties.get(name):
            return str(properties[name])
    return key


def neighbourhood(
    db: Driver,
    scope: Scope,
    label: str,
    key: str,
    depth: int = 1,
    keep_labels: list[str] | None = None,
    keep_types: list[str] | None = None,
) -> dict:
    """Nodes and relationships within `depth` hops of one node, limited to chosen labels and relationship types.

    Depth, labels and types are checked against fixed lists before they reach Cypher; the key and scope are parameters.
    """
    if label not in labels():
        raise ValueError(f"Unknown label {label}")
    if not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"Depth must be between 1 and {MAX_DEPTH}")
    keep_labels = [x for x in (keep_labels or labels()) if x in labels()]
    keep_types = [x for x in (keep_types or relationship_types()) if x in relationship_types()]
    in_scope = "(n.sourceSystem IS NULL OR n.sourceSystem IN $systems) AND (n.companyCode IS NULL OR n.companyCode IN $companies)"
    query = f"""
        MATCH (start:{label} {{key: $key}})
        WHERE {in_scope.replace("n.", "start.")}
        OPTIONAL MATCH p = (start)-[rels*1..{depth}]-(other)
        WHERE all(r IN rels WHERE type(r) IN $types)
          AND all(n IN nodes(p)[1..] WHERE any(l IN labels(n) WHERE l IN $labels) AND {in_scope})
        RETURN start, p LIMIT {MAX_PATHS}
    """
    nodes: dict[str, dict] = {}
    edges: set[tuple[str, str, str]] = set()
    with db.session(default_access_mode=READ_ACCESS) as session:
        records = list(
            session.run(
                query,
                key=key,
                systems=scope.source_systems,
                companies=scope.company_codes,
                types=keep_types,
                labels=keep_labels,
            )
        )

    def add(node) -> str:
        properties = dict(node)
        nodes.setdefault(
            node.element_id,
            {
                "label": next(iter(node.labels)),
                "key": properties.get("key"),
                "name": _node_name(properties, properties.get("key", "")),
                "properties": mask({k: v for k, v in properties.items() if k != "key"}),
            },
        )
        return node.element_id

    for record in records:
        add(record["start"])
        path = record["p"]
        if path is None:
            continue
        for node in path.nodes:
            add(node)
        for rel in path.relationships:
            edges.add((add(rel.start_node), add(rel.end_node), rel.type))
    start = next((element for element, n in nodes.items() if n["key"] == key and n["label"] == label), None)
    return {
        "found": bool(records),
        "start": start,
        "nodes": nodes,
        "edges": sorted(edges),
        "truncated": len(records) >= MAX_PATHS,
    }


def _quote(text: object) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def neighbourhood_dot(around: dict) -> str:
    lines = [
        "digraph neighbourhood {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded", fontsize=10];',
        "  edge [fontsize=9];",
    ]
    for element, node in around["nodes"].items():
        extra = ", penwidth=2.5" if element == around["start"] else ""
        lines.append(f"  {_quote(element)} [label={_quote(node['label'] + chr(10) + node['name'])}{extra}];")
    for source, target, rel_type in around["edges"]:
        lines.append(f"  {_quote(source)} -> {_quote(target)} [label={_quote(rel_type)}];")
    lines.append("}")
    return "\n".join(lines)


def chain_gaps(db: Driver, scope: Scope) -> dict:
    """The Cypher library's chain-gap query, limited to the reviewer's source systems, with counts by gap type."""
    query = (QUERIES_DIR / "chain_gaps.cypher").read_text(encoding="utf-8")
    with db.session(default_access_mode=READ_ACCESS) as session:
        rows = [r.data() for r in session.run(query)]
    rows = sorted(
        (r for r in rows if r["source_system"] in scope.source_systems),
        key=lambda r: (r["gap"], r["source_system"], str(r["document"])),
    )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["gap"]] = counts.get(row["gap"], 0) + 1
    return {"counts": [{"gap": g, "documents": n} for g, n in sorted(counts.items())], "rows": rows}


# ---------------------------------------------------------------- supplier resolution


def golden_suppliers(conn: psycopg.Connection) -> list[dict]:
    """Golden records, largest clusters first: a cluster of one is a supplier no other record resolved to."""
    return conn.execute(
        """
        select supplier_gid, golden_name, vat_number, country_code, member_count
        from mdm.supplier_golden order by member_count desc, supplier_gid
        """
    ).fetchall()


def golden_detail(conn: psycopg.Connection, supplier_gid: str) -> dict:
    """Members with how each was matched, the candidate pairs among them or touching them, and steward decisions."""
    members = conn.execute(
        """
        select x.source_system, x.source_supplier_id, s.supplier_name, s.vat_number, s.country_code, s.company_code,
               x.match_method, x.match_score
        from mdm.supplier_xref x
        join core.supplier s using (source_system, source_supplier_id)
        where x.supplier_gid = %s order by 1, 2
        """,
        (supplier_gid,),
    ).fetchall()
    keys = [f"{m['source_system']}:{m['source_supplier_id']}" for m in members]
    pairs = conn.execute(
        """
        select c.a_source_system || ':' || c.a_source_supplier_id as record_a,
               c.b_source_system || ':' || c.b_source_supplier_id as record_b,
               c.method, c.name_score, c.address_score, c.combined_score, c.decision, c.evidence
        from mdm.supplier_match_candidate c
        where c.a_source_system || ':' || c.a_source_supplier_id = any(%(keys)s)
           or c.b_source_system || ':' || c.b_source_supplier_id = any(%(keys)s)
        order by 1, 2
        """,
        {"keys": keys},
    ).fetchall()
    for pair in pairs:
        pair["evidence"] = mask(pair["evidence"])
    decisions = conn.execute(
        """
        select decision_id, a_source_system || ':' || a_source_supplier_id as record_a,
               b_source_system || ':' || b_source_supplier_id as record_b, decision, decided_by, decided_at
        from mdm.steward_decision
        where a_source_system || ':' || a_source_supplier_id = any(%(keys)s)
           or b_source_system || ':' || b_source_supplier_id = any(%(keys)s)
        order by decision_id
        """,
        {"keys": keys},
    ).fetchall()
    return {"members": members, "pairs": pairs, "steward_decisions": decisions}


def steward_queue(conn: psycopg.Connection) -> list[dict]:
    """Pairs sent to a steward. A pair with a decision recorded takes effect on the next resolve-suppliers run."""
    rows = conn.execute(
        """
        select c.a_source_system || ':' || c.a_source_supplier_id as record_a,
               c.b_source_system || ':' || c.b_source_supplier_id as record_b,
               c.method, c.combined_score, c.evidence,
               d.decision as steward_decision, d.decided_by, d.decided_at
        from mdm.supplier_match_candidate c
        left join lateral (
            select s.decision, s.decided_by, s.decided_at from mdm.steward_decision s
            where s.a_source_system = c.a_source_system and s.a_source_supplier_id = c.a_source_supplier_id
              and s.b_source_system = c.b_source_system and s.b_source_supplier_id = c.b_source_supplier_id
            order by s.decision_id desc limit 1
        ) d on true
        where c.decision = 'STEWARD_REVIEW'
        order by c.combined_score desc, 1, 2
        """
    ).fetchall()
    for row in rows:
        row["evidence"] = mask(row["evidence"])
    return rows
