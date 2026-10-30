"""Rebuild the Neo4j projection of core, mdm and current recon from ontology/mapping.yaml.

PostgreSQL stays authoritative. A sync wipes the graph and reloads it, which is idempotent by construction and needs
no change tracking: at this size a rebuild takes seconds. Neo4j has no exact decimal type, so money is projected as
strings. The graph is for navigating relationships; arithmetic that matters stays in SQL.
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from neo4j import Driver, GraphDatabase

from p2p_recon.config import GRAPH_DIR, get_settings
from p2p_recon.db import connect, pipeline_run
from p2p_recon.ontology import class_rows, load_mapping, relationship_rows

BATCH_SIZE = 500
QUERIES_DIR = GRAPH_DIR / "queries"


def driver() -> Driver:
    settings = get_settings()
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))


def _value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | int | float | bool) or value is None:
        return value
    return str(value).strip()


def _batches(rows: list[dict]) -> list[list[dict]]:
    return [rows[i : i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]


def sync_graph() -> dict:
    mapping = load_mapping()
    with pipeline_run("graph_sync") as run, connect() as conn, driver() as db, db.session() as session:
        session.run("MATCH (n) DETACH DELETE n").consume()

        expected_nodes: dict[str, int] = {}
        for class_name in mapping["classes"]:
            # Labels are constants from mapping.yaml. Community Edition has no node-key constraint, so the composite
            # business key is folded into one 'key' property with a uniqueness constraint.
            session.run(
                f"CREATE CONSTRAINT {class_name.lower()}_key IF NOT EXISTS FOR (n:{class_name}) REQUIRE n.key IS UNIQUE"
            ).consume()
            rows = [
                {
                    "key": r["key"],
                    "props": {k: _value(v) for k, v in r.items() if k != "key" and v is not None},
                }
                for r in class_rows(conn, class_name)
            ]
            for batch in _batches(rows):
                session.run(
                    f"UNWIND $rows AS row CREATE (n:{class_name} {{key: row.key}}) SET n += row.props",
                    rows=batch,
                ).consume()
            expected_nodes[class_name] = len(rows)
        session.run(
            "CREATE INDEX invoice_number IF NOT EXISTS FOR (n:SupplierInvoice) ON (n.documentNumber)"
        ).consume()

        expected_edges: dict[tuple[str, str, str], int] = {}
        for rel in mapping["relationships"]:
            rows = relationship_rows(conn, rel)
            for batch in _batches(rows):
                session.run(
                    f"UNWIND $rows AS row MATCH (a:{rel['from']} {{key: row.from_key}}) MATCH (b:{rel['to']} {{key: row.to_key}}) "
                    f"CREATE (a)-[:{rel['type']}]->(b)",
                    rows=batch,
                ).consume()
            expected_edges[(rel["from"], rel["type"], rel["to"])] = len(rows)

        # A reconciliation, not a formality: an edge whose node is missing fails silently in the MATCH above.
        results = []
        for class_name, expected in expected_nodes.items():
            actual = session.run(f"MATCH (n:{class_name}) RETURN count(n) AS c").single()["c"]
            results.append((f"graph.nodes.{class_name}", expected, actual))
        for (source, rel_type, target), expected in expected_edges.items():
            actual = session.run(
                f"MATCH (:{source})-[r:{rel_type}]->(:{target}) RETURN count(r) AS c"
            ).single()["c"]
            results.append((f"graph.edges.{source}.{rel_type}.{target}", expected, actual))
        for check, expected, actual in results:
            conn.execute(
                "insert into ops.dq_result (run_id, check_name, expected, actual, status) values (%s, %s, %s, %s, %s)",
                (run.run_id, check, expected, actual, "pass" if expected == actual else "fail"),
            )
        run.lineage(conn, "core+mdm+recon", "neo4j", sum(expected_nodes.values()))
        failed = [check for check, expected, actual in results if expected != actual]
        run.counts = {
            "nodes": sum(expected_nodes.values()),
            "edges": sum(expected_edges.values()),
            "reconciliation_failures": failed,
        }
        return run.counts


def available_queries() -> list[str]:
    return sorted(path.stem for path in QUERIES_DIR.glob("*.cypher"))


def run_query(name: str, **parameters: object) -> list[dict]:
    path: Path = QUERIES_DIR / f"{name}.cypher"
    if not path.exists():
        raise ValueError(f"Unknown query {name}. Available: {', '.join(available_queries())}")
    with driver() as db, db.session() as session:
        return [record.data() for record in session.run(path.read_text(encoding="utf-8"), **parameters)]
