"""What the ontology says, what it covers and what depends on it, for the governance reviewer.

The Turtle files, mapping.yaml and the contracts are read from the repository: they are the same for every role, so
parsing them once per process (lru_cache) cannot serve one role's data to another. Counts come from PostgreSQL as the
reviewer, and graph counts from what the last graph sync recorded, so viewing the page never touches Neo4j.
"""

from functools import lru_cache

import psycopg
from rdflib import OWL, RDF, RDFS, SH, SKOS, BNode, Graph, Literal, URIRef
from rdflib.collection import Collection

from p2p_recon import contracts
from p2p_recon.admin import rules
from p2p_recon.agents.providers import OfflineProvider
from p2p_recon.config import ONTOLOGY_DIR
from p2p_recon.ontology import P2P, VOC, load_mapping

TURTLE_FILES = ("p2p.ttl", "vocab.ttl", "shapes.ttl")
# The reviewer may not read bank numbers, so BankAccount is counted through the masked view, which has the same rows.
COUNT_THROUGH = {"core.supplier_bank_account": "core.v_supplier_bank_account_masked"}


@lru_cache
def _graph(name: str) -> Graph:
    return Graph().parse(ONTOLOGY_DIR / name)


def turtle_files() -> dict[str, bytes]:
    return {name: (ONTOLOGY_DIR / name).read_bytes() for name in TURTLE_FILES}


def _local(node: object) -> str:
    text = str(node)
    for namespace in (str(P2P), str(VOC)):
        if text.startswith(namespace):
            return text.removeprefix(namespace)
    return text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _text(graph: Graph, node: object, predicate: URIRef, language: str = "en") -> str | None:
    values = [v for v in graph.objects(node, predicate) if isinstance(v, Literal)]
    chosen = [str(v) for v in values if v.language == language] or (
        [str(v) for v in values if v.language is None] if language == "en" else []
    )
    return ", ".join(sorted(chosen)) or None


def metadata() -> dict:
    graph = _graph("p2p.ttl")
    ontology = next(graph.subjects(RDF.type, OWL.Ontology))
    return {
        "iri": str(ontology),
        "version": str(graph.value(ontology, OWL.versionInfo)),
        "label": _text(graph, ontology, RDFS.label),
        "mapping_version": str(load_mapping()["version"]),
    }


# ---------------------------------------------------------------- what it says


def classes() -> list[dict]:
    graph, mapping = _graph("p2p.ttl"), load_mapping()["classes"]
    rows = []
    for node in sorted(graph.subjects(RDF.type, OWL.Class), key=_local):
        name = _local(node)
        rows.append(
            {
                "class": name,
                "parent": ", ".join(sorted(_local(p) for p in graph.objects(node, RDFS.subClassOf))) or None,
                "label": _text(graph, node, RDFS.label),
                "also_called": _text(graph, node, SKOS.altLabel),
                "definition": _text(graph, node, SKOS.definition),
                "table": mapping.get(name, {}).get("table"),
            }
        )
    return rows


def hierarchy_dot() -> str:
    """rdfs:subClassOf as a drawing. Classes with no parent and no children are listed in the table only."""
    graph = _graph("p2p.ttl")
    edges = sorted(
        (_local(child), _local(parent)) for child, parent in graph.subject_objects(RDFS.subClassOf)
    )
    lines = ["digraph hierarchy {", "  rankdir=BT;", '  node [shape=box, style="rounded", fontsize=11];']
    lines += [f'  "{child}" -> "{parent}" [arrowhead=empty];' for child, parent in edges]
    lines.append("}")
    return "\n".join(lines)


def properties() -> list[dict]:
    graph = _graph("p2p.ttl")
    rows = []
    for kind, rdf_type in (("object", OWL.ObjectProperty), ("datatype", OWL.DatatypeProperty)):
        for node in sorted(graph.subjects(RDF.type, rdf_type), key=_local):
            rows.append(
                {
                    "property": _local(node),
                    "kind": kind,
                    "domain": ", ".join(sorted(_local(d) for d in graph.objects(node, RDFS.domain))) or None,
                    "range": ", ".join(sorted(_local(r) for r in graph.objects(node, RDFS.range))) or None,
                    "functional": (node, RDF.type, OWL.FunctionalProperty) in graph,
                    "definition": _text(graph, node, SKOS.definition),
                }
            )
    return rows


def concepts() -> list[dict]:
    """Every SKOS concept with English and German labels. German appears where the vocabulary gives one."""
    graph = _graph("vocab.ttl")
    rows = []
    for node in sorted(graph.subjects(RDF.type, SKOS.Concept), key=_local):
        rows.append(
            {
                "scheme": ", ".join(sorted(_local(s) for s in graph.objects(node, SKOS.inScheme))),
                "concept": _local(node),
                "label_en": _text(graph, node, SKOS.prefLabel),
                "label_de": _text(graph, node, SKOS.prefLabel, "de"),
                "also_en": _text(graph, node, SKOS.altLabel),
                "also_de": _text(graph, node, SKOS.altLabel, "de"),
                "broader": ", ".join(sorted(_local(b) for b in graph.objects(node, SKOS.broader))) or None,
                "definition": _text(graph, node, SKOS.definition),
            }
        )
    return rows


def _term(graph: Graph, node: object) -> str:
    if isinstance(node, BNode) and (node, RDF.first, None) in graph:
        return "(" + " ".join(_local(item) for item in Collection(graph, node)) + ")"
    return _local(node) if isinstance(node, URIRef) else str(node)


CONSTRAINTS = (
    SH.minCount,
    SH.maxCount,
    SH.datatype,
    SH["class"],
    SH["in"],
    SH.pattern,
    SH.minInclusive,
    SH.minExclusive,
)


def shapes() -> list[dict]:
    """Each node shape, its property constraints and its SPARQL constraints, with the SPARQL as written."""
    graph = _graph("shapes.ttl")
    rows = []
    for shape in sorted(graph.subjects(RDF.type, SH.NodeShape), key=_local):
        targets = sorted(_local(t) for t in graph.objects(shape, SH.targetClass))
        for prop in graph.objects(shape, SH.property):
            rules_text = [
                f"{_local(c)} {_term(graph, value)}" for c in CONSTRAINTS for value in graph.objects(prop, c)
            ]
            rows.append(
                {
                    "shape": _local(shape),
                    "targets": ", ".join(targets),
                    "path": _local(graph.value(prop, SH.path)),
                    "constraint": "; ".join(rules_text),
                    "message": None,
                    "sparql": None,
                }
            )
        for constraint in graph.objects(shape, SH.sparql):
            rows.append(
                {
                    "shape": _local(shape),
                    "targets": ", ".join(targets),
                    "path": None,
                    "constraint": "SPARQL",
                    "message": str(graph.value(constraint, SH.message)),
                    "sparql": str(graph.value(constraint, SH.select)).strip(),
                }
            )
    return rows


# ---------------------------------------------------------------- what it covers


def _count(conn: psycopg.Connection, table: str) -> int:
    source = COUNT_THROUGH.get(table, table)
    schema, name = source.split(".")
    has_flag = conn.execute(
        "select 1 from information_schema.columns where table_schema = %s and table_name = %s and column_name = 'is_deleted'",
        (schema, name),
    ).fetchone()
    # Table names come from mapping.yaml, never from the page.
    where = " where not is_deleted" if has_flag else ""
    return conn.execute(f"select count(*) as n from {source}{where}").fetchone()["n"]


def _graph_counts(conn: psycopg.Connection) -> tuple[int | None, dict[str, int]]:
    run = conn.execute(
        "select max(run_id) as run_id from ops.pipeline_run where step = 'graph_sync' and status = 'succeeded'"
    ).fetchone()["run_id"]
    rows = conn.execute(
        "select check_name, actual from ops.dq_result where run_id = %s and check_name like 'graph.nodes.%%'",
        (run,),
    ).fetchall()
    return run, {r["check_name"].removeprefix("graph.nodes."): int(r["actual"]) for r in rows}


def coverage(conn: psycopg.Connection) -> dict:
    """Class -> table -> graph label -> rows -> nodes. A gap is an unmapped class, or rows and nodes that disagree."""
    mapping = load_mapping()["classes"]
    run, nodes = _graph_counts(conn)
    rows = []
    for entry in classes():
        name, spec = entry["class"], mapping.get(entry["class"])
        row_count = _count(conn, spec["table"]) if spec else None
        node_count = nodes.get(name) if spec else None
        if not spec:
            gap = "not mapped: no table or graph label"
        elif node_count is None:
            gap = "no graph count recorded"
        elif row_count != node_count:
            gap = f"rows and nodes differ by {row_count - node_count}"
        elif row_count == 0:
            gap = "mapped but empty"
        else:
            gap = None
        rows.append(
            {
                "class": name,
                "table": spec["table"] if spec else None,
                "graph_label": name if spec else None,
                "rows": row_count,
                "nodes": node_count,
                "gap": gap,
            }
        )
    return {"graph_sync_run_id": run, "rows": rows}


def _scheme_codes(scheme: str) -> tuple[set[str], set[str]]:
    """(all concepts, leaf concepts) of a scheme. A concept with narrower ones is a grouping, never written by recon."""
    graph = _graph("vocab.ttl")
    members = {_local(s) for s in graph.subjects(SKOS.inScheme, VOC[scheme])}
    parents = {_local(o) for s, o in graph.subject_objects(SKOS.broader)}
    return members, members - parents


def drift(conn: psycopg.Connection) -> dict:
    """Codes recon writes against the vocabulary, both ways, and concepts without a definition."""
    status_used = {
        r["c"]: r["n"]
        for r in conn.execute(
            "select status_code as c, count(*) as n from recon.current_match_result group by 1"
        )
    }
    reason_used = {
        r["c"]: r["n"]
        for r in conn.execute(
            """
            select c, count(*) as n from (
                select reason_code as c from recon.current_exception
                union all select reason_code from recon.current_control_breach
            ) u group by 1
            """
        )
    }
    ever_used = {
        r["c"]
        for r in conn.execute(
            "select distinct status_code as c from recon.match_result union select distinct reason_code from recon.exception"
        )
    }
    statuses, status_leaves = _scheme_codes("MatchStatus")
    reasons, reason_leaves = _scheme_codes("ExceptionReason")
    sql_codes = rules.sql_reason_codes()
    missing_definition = [
        {"scheme": c["scheme"], "concept": c["concept"]}
        for c in concepts()
        if c["scheme"] in ("MatchStatus", "ExceptionReason")
        and not c["definition"]
        and c["concept"] in (status_leaves | reason_leaves)
    ]
    return {
        "used_not_in_vocabulary": sorted(
            [
                {"code": c, "column": "status_code", "rows": n}
                for c, n in status_used.items()
                if c not in statuses
            ]
            + [
                {"code": c, "column": "reason_code", "rows": n}
                for c, n in reason_used.items()
                if c not in reasons
            ],
            key=lambda r: r["code"],
        ),
        "in_sql_not_in_vocabulary": sorted(sql_codes - reasons),
        "not_used_in_latest_run": sorted(
            (status_leaves - set(status_used)) | (reason_leaves - set(reason_used))
        ),
        "never_used": sorted((status_leaves | reason_leaves) - ever_used),
        "missing_definition": missing_definition,
        "usage": sorted(
            [{"code": c, "scheme": "MatchStatus", "rows": n} for c, n in status_used.items()]
            + [{"code": c, "scheme": "ExceptionReason", "rows": n} for c, n in reason_used.items()],
            key=lambda r: (r["scheme"], r["code"]),
        ),
    }


def latest_validation(conn: psycopg.Connection) -> dict:
    """The last recorded SHACL run: each example and sampled chain, with SQL's reasons beside a chain that failed."""
    run = conn.execute(
        "select max(run_id) as run_id from ops.pipeline_run where step = 'validate_ontology' and status = 'succeeded'"
    ).fetchone()["run_id"]
    if run is None:
        return {"run_id": None, "rows": []}
    rows = conn.execute(
        """
        select d.check_name, d.status, d.actual as violations, d.detail,
               (select array_agg(distinct e.reason_code order by e.reason_code) from recon.current_exception e
                where d.check_name like 'shacl.chain.%%'
                  and e.source_system || ':' || e.invoice_id = substr(d.check_name, length('shacl.chain.') + 1)) as sql_reasons
        from ops.dq_result d where d.run_id = %s and d.check_name like 'shacl.%%'
        order by d.status, d.check_name
        """,
        (run,),
    ).fetchall()
    return {"run_id": run, "rows": rows}


# ---------------------------------------------------------------- change impact


def terms() -> list[tuple[str, str]]:
    """(kind, name) for every class, property and concept, for the impact selector."""
    return (
        [("class", c["class"]) for c in classes()]
        + [("property", p["property"]) for p in properties()]
        + [("concept", c["concept"]) for c in concepts()]
    )


def _contracts_using(tables: set[str], columns: set[tuple[str, str]] | None = None) -> list[str]:
    found = []
    for name, contract in contracts.load_contracts().items():
        for table, spec in contract["tables"].items():
            hit = (
                table in tables
                if columns is None
                else any((table, c) in columns for c in spec.get("columns", {}))
            )
            if hit:
                found.append(f"{name} {contract['version']} ({table})")
    return sorted(set(found))


def _rules_reading(tables: set[str]) -> list[dict]:
    return [
        {"rule": code, "scenarios": rule["scenarios"]}
        for code, rule in rules.registry()["rules"].items()
        if tables & set(rule["tables"])
    ]


def _shapes_mentioning(name: str) -> list[str]:
    """Node shapes that target the term, constrain it as a path, name it in a constraint (sh:class, sh:in) or in SPARQL."""
    found = set()
    for row in shapes():
        words = set(row["constraint"].translate(str.maketrans("();", "   ")).split())
        sparql = row["sparql"] or ""
        if (
            name == row["path"]
            or name in row["targets"].split(", ")
            or name in words
            or f"p2p:{name}" in sparql
            or f"voc:{name}" in sparql
        ):
            found.add(row["shape"])
    return sorted(found)


def impact(kind: str, name: str) -> dict:
    """What depends on one class, property or concept: tables, columns, relationship types, shapes, contracts, rules,
    planted scenarios, and for a concept the action the offline provider maps it to."""
    mapping = load_mapping()
    tables: set[str] = set()
    columns: set[tuple[str, str]] = set()
    relationships: list[str] = []
    if kind == "class":
        spec = mapping["classes"].get(name)
        if spec:
            tables.add(spec["table"])
            columns |= {(spec["table"], c) for c in (*spec.get("properties", {}).values(), *spec["key"])}
        for rel in mapping["relationships"]:
            if name in (rel["from"], rel["to"]):
                relationships.append(f"({rel['from']})-[:{rel['type']}]->({rel['to']})")
                tables.add(rel.get("from_table", mapping["classes"][rel["from"]]["table"]))
        subclasses = [c["class"] for c in classes() if c["parent"] and name in c["parent"].split(", ")]
        using = [p["property"] for p in properties() if name in (p["domain"], p["range"])]
        return {
            "tables": sorted(tables),
            "columns": sorted(f"{t}.{c}" for t, c in columns),
            "relationship_types": relationships,
            "subclasses": subclasses,
            "properties_with_this_domain_or_range": using,
            "shapes": _shapes_mentioning(name),
            "contracts": _contracts_using(tables),
            "rules_reading_these_tables": _rules_reading(tables),
        }
    if kind == "property":
        for spec in mapping["classes"].values():
            column = {**spec.get("properties", {}), **spec.get("concepts", {})}.get(name)
            if column:
                tables.add(spec["table"])
                columns.add((spec["table"], column))
        for rel in mapping["relationships"]:
            if rel["property"] == name:
                from_table = rel.get("from_table", mapping["classes"][rel["from"]]["table"])
                relationships.append(f"({rel['from']})-[:{rel['type']}]->({rel['to']})")
                tables.add(from_table)
                columns |= {(from_table, c) for c in rel["join"]}
        return {
            "tables": sorted(tables),
            "columns": sorted(f"{t}.{c}" for t, c in columns),
            "relationship_types": relationships,
            "shapes": _shapes_mentioning(name),
            "contracts": _contracts_using(tables, columns),
            "rules_reading_these_tables": _rules_reading(tables),
        }
    rule = rules.registry()["rules"].get(name)
    columns_holding = [
        spec["column"]
        for spec in mapping["vocabularies"].values()
        if name in _scheme_codes(spec["scheme"])[0]
    ]
    offline = OfflineProvider.RULES.get(name) or OfflineProvider.STATUS_RULES.get(name)
    return {
        "columns": columns_holding,
        "shapes": _shapes_mentioning(name),
        "rule": rule["description"] if rule else None,
        "rules_reading_these_tables": [{"rule": name, "scenarios": rule["scenarios"]}] if rule else [],
        "offline_action": offline[0] if offline else None,
        "contracts": _contracts_using({c.rsplit(".", 1)[0] for c in columns_holding}),
    }
