"""The ontology as code: load it, validate instance data against SHACL, read the mapping, export rows as RDF.

mapping.yaml drives the export here and the Neo4j projection in graph.py, so the two cannot disagree on naming.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache

import psycopg
import yaml
from pyshacl import validate
from rdflib import RDF, SH, XSD, Graph, Literal, Namespace, URIRef
from rdflib.namespace import SKOS

from p2p_recon.config import ONTOLOGY_DIR

P2P = Namespace("https://example.org/p2p#")
VOC = Namespace("https://example.org/p2p/vocab#")
DATA = Namespace("https://example.org/p2p/data/")


def ontology_graph() -> Graph:
    return Graph().parse(ONTOLOGY_DIR / "p2p.ttl").parse(ONTOLOGY_DIR / "vocab.ttl")


def shapes_graph() -> Graph:
    return Graph().parse(ONTOLOGY_DIR / "shapes.ttl")


@dataclass(frozen=True)
class ShaclResult:
    conforms: bool
    messages: list[tuple[str, str]]  # (focus node's local name, message)


def validate_graph(data: Graph) -> ShaclResult:
    conforms, report, _ = validate(
        data, shacl_graph=shapes_graph(), ont_graph=ontology_graph(), inference="rdfs", advanced=True
    )
    messages = sorted(
        (
            str(report.value(result, SH.focusNode)).rsplit("/", 1)[-1],
            str(report.value(result, SH.resultMessage)),
        )
        for result in report.subjects(object=SH.ValidationResult)
    )
    return ShaclResult(conforms, messages)


def concept_codes(scheme: str) -> set[str]:
    """Local names of the concepts in a SKOS scheme: the only codes recon may write."""
    graph = Graph().parse(ONTOLOGY_DIR / "vocab.ttl")
    return {str(s).removeprefix(str(VOC)) for s in graph.subjects(SKOS.inScheme, VOC[scheme])}


@lru_cache
def load_mapping() -> dict:
    return yaml.safe_load((ONTOLOGY_DIR / "mapping.yaml").read_text(encoding="utf-8"))


def _key_sql(alias: str, columns: list[str]) -> str:
    return "concat_ws('|', " + ", ".join(f"{alias}.{c}::text" for c in columns) + ")"


def _not_deleted(conn: psycopg.Connection, table: str, alias: str) -> str:
    schema, name = table.split(".")
    has_flag = conn.execute(
        "select 1 from information_schema.columns where table_schema = %s and table_name = %s and column_name = 'is_deleted'",
        (schema, name),
    ).fetchone()
    return f"not {alias}.is_deleted" if has_flag else "true"


def class_rows(conn: psycopg.Connection, class_name: str) -> list[dict]:
    """One dict per instance: 'key', the projected properties and concepts, named by the ontology."""
    spec = load_mapping()["classes"][class_name]
    selected = {**spec.get("properties", {}), **spec.get("concepts", {})}
    columns = ", ".join(f'n.{column} as "{prop}"' for prop, column in selected.items())
    return conn.execute(
        f"select {_key_sql('n', spec['key'])} as key{', ' + columns if columns else ''} "
        f"from {spec['table']} n where {_not_deleted(conn, spec['table'], 'n')}"
    ).fetchall()


def relationship_rows(conn: psycopg.Connection, rel: dict) -> list[dict]:
    """One dict per edge, with from_key and to_key. Columns are constants from mapping.yaml, never input."""
    classes = load_mapping()["classes"]
    source, target = classes[rel["from"]], classes[rel["to"]]
    from_table = rel.get("from_table", source["table"])
    on = " and ".join(f"f.{a} = t.{b}" for a, b in rel["join"].items())
    present = " and ".join(f"f.{c} is not null" for c in source["key"])
    return conn.execute(
        f"select distinct {_key_sql('f', source['key'])} as from_key, {_key_sql('t', target['key'])} as to_key "
        f"from {from_table} f join {target['table']} t on {on} "
        f"where {present} and {_not_deleted(conn, from_table, 'f')} and {_not_deleted(conn, target['table'], 't')}"
    ).fetchall()


def _literal(value: object) -> Literal:
    if isinstance(value, Decimal):
        return Literal(value, datatype=XSD.decimal)
    if isinstance(value, bool):
        return Literal(value, datatype=XSD.boolean)
    if isinstance(value, int):
        return Literal(value, datatype=XSD.integer)
    if isinstance(value, datetime):
        return Literal(value, datatype=XSD.dateTime)
    if isinstance(value, date):
        return Literal(value, datatype=XSD.date)
    # A plain literal: sh:in in the shapes compares by term, and "x" and "x"^^xsd:string are different terms in rdflib.
    return Literal(str(value).strip())


def _iri(class_name: str, key: str) -> URIRef:
    return DATA[f"{class_name}/" + key.replace("|", "-").replace(" ", "_")]


def export_rdf(conn: psycopg.Connection) -> Graph:
    """The whole canonical model as RDF, named by the ontology, for SHACL validation."""
    mapping = load_mapping()
    graph = Graph()
    for class_name, spec in mapping["classes"].items():
        concepts = spec.get("concepts", {})
        for row in class_rows(conn, class_name):
            subject = _iri(class_name, row["key"])
            graph.add((subject, RDF.type, P2P[class_name]))
            for prop in spec.get("properties", {}):
                if row[prop] is not None:
                    graph.add((subject, P2P[prop], _literal(row[prop])))
            for prop in concepts:
                graph.add((subject, P2P[prop], VOC[row[prop]]))
    for rel in mapping["relationships"]:
        for row in relationship_rows(conn, rel):
            graph.add(
                (_iri(rel["from"], row["from_key"]), P2P[rel["property"]], _iri(rel["to"], row["to_key"]))
            )
    return graph


def document_closure(graph: Graph, start: list[URIRef]) -> Graph:
    """Everything reachable from the start nodes by following object properties forward."""
    subset, seen, frontier = Graph(), set(), list(start)
    while frontier:
        node = frontier.pop()
        if node in seen:
            continue
        seen.add(node)
        for predicate, obj in graph.predicate_objects(node):
            subset.add((node, predicate, obj))
            if isinstance(obj, URIRef) and str(obj).startswith(str(DATA)):
                frontier.append(obj)
    return subset


def invoice_chain_nodes(conn: psycopg.Connection, source_system: str, invoice_id: str) -> list[URIRef]:
    """The documents of one invoice's chain, as export_rdf names them."""
    rows = conn.execute(
        """
        select distinct l.po_number, pl.pr_number, g.grn_number, l.line_number
        from core.invoice_line l
        left join core.po_line pl on pl.source_system = l.source_system and pl.po_number = l.po_number and pl.line_number = l.po_line_number
        left join core.grn_line g on g.source_system = l.source_system and g.po_number = l.po_number and g.po_line_number = l.po_line_number
        where l.source_system = %s and l.invoice_id = %s
        """,
        (source_system, invoice_id),
    ).fetchall()
    nodes = [_iri("SupplierInvoice", f"{source_system}|{invoice_id}")]
    for row in rows:
        nodes.append(_iri("MatchResult", f"{source_system}|{invoice_id}|{row['line_number']}"))
        if row["po_number"]:
            nodes.append(_iri("PurchaseOrder", f"{source_system}|{row['po_number']}"))
        if row["pr_number"]:
            nodes.append(_iri("PurchaseRequisition", f"{source_system}|{row['pr_number']}"))
        if row["grn_number"]:
            nodes.append(_iri("GoodsReceipt", f"{source_system}|{row['grn_number']}"))
    return nodes
