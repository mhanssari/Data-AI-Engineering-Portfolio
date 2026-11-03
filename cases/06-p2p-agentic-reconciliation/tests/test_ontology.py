"""The ontology is valid, the examples behave as designed, and the mapping and the database agree with it."""

import pytest
from rdflib import OWL, RDF, Graph

from p2p_recon.config import ONTOLOGY_DIR
from p2p_recon.ontology import (
    P2P,
    concept_codes,
    document_closure,
    export_rdf,
    invoice_chain_nodes,
    load_mapping,
    ontology_graph,
    validate_graph,
)


@pytest.mark.parametrize(
    "name", ["p2p.ttl", "vocab.ttl", "shapes.ttl", "examples/valid-chain.ttl", "examples/invalid-chain.ttl"]
)
def test_turtle_parses(name):
    assert len(Graph().parse(ONTOLOGY_DIR / name)) > 0


def test_valid_example_conforms():
    result = validate_graph(Graph().parse(ONTOLOGY_DIR / "examples" / "valid-chain.ttl"))
    assert result.conforms, result.messages


def test_invalid_example_raises_exactly_the_planted_violations():
    # Exact, because a shape that stops firing is as much a defect as one that fires wrongly.
    result = validate_graph(Graph().parse(ONTOLOGY_DIR / "examples" / "invalid-chain.ttl"))
    assert not result.conforms
    messages = " | ".join(message for _, message in result.messages)
    assert len(result.messages) == 5, result.messages
    assert "Segregation of duties" in messages
    assert "Approval limit exceeded" in messages
    assert "Exception reason must be a concept" in messages


def test_every_mapped_name_is_defined_in_the_ontology():
    graph = ontology_graph()
    classes = set(graph.subjects(RDF.type, OWL.Class))
    properties = set(graph.subjects(RDF.type, OWL.ObjectProperty)) | set(
        graph.subjects(RDF.type, OWL.DatatypeProperty)
    )
    mapping = load_mapping()
    undefined = []
    for name, spec in mapping["classes"].items():
        if P2P[name] not in classes:
            undefined.append(f"class {name}")
        for prop in [*spec.get("properties", {}), *spec.get("concepts", {})]:
            if P2P[prop] not in properties:
                undefined.append(f"{name}.{prop}")
    undefined += [
        rel["property"] for rel in mapping["relationships"] if P2P[rel["property"]] not in properties
    ]
    assert not undefined, undefined


def test_every_mapped_column_exists_in_the_database(pipeline, conn):
    rows = conn.execute(
        "select table_schema || '.' || table_name as t, column_name as c from information_schema.columns"
    ).fetchall()
    columns = {(row["t"], row["c"]) for row in rows}
    mapping = load_mapping()
    missing = []
    for name, spec in mapping["classes"].items():
        wanted = [*spec["key"], *spec.get("properties", {}).values(), *spec.get("concepts", {}).values()]
        missing += [f"{name}: {spec['table']}.{c}" for c in wanted if (spec["table"], c) not in columns]
    for rel in mapping["relationships"]:
        source, target = mapping["classes"][rel["from"]], mapping["classes"][rel["to"]]
        from_table = rel.get("from_table", source["table"])
        missing += [
            f"{rel['property']}: {from_table}.{a}" for a in rel["join"] if (from_table, a) not in columns
        ]
        missing += [
            f"{rel['property']}: {target['table']}.{b}"
            for b in rel["join"].values()
            if (target["table"], b) not in columns
        ]
    assert not missing, missing


def test_bank_account_number_is_never_projected():
    # The mapping is an allowlist for both projections. This control keeps a real IBAN out of the graph.
    assert "account_number" not in load_mapping()["classes"]["BankAccount"]["properties"].values()


def test_vocabulary_covers_all_planted_reasons(pipeline):
    reasons = concept_codes("ExceptionReason")
    planted = {
        code
        for row in pipeline.expected
        if row["document_type"] != "SUPPLIER_PAIR"
        for code in row["expected_reason_codes"].split("|")
        if code
    }
    assert planted <= reasons, planted - reasons


@pytest.fixture(scope="module")
def exported(pipeline):
    from p2p_recon.db import connect

    with connect() as connection:
        return export_rdf(connection)


def _invoice_for(pipeline, scenario):
    return next(
        r for r in pipeline.expected if r["scenario_id"] == scenario and r["document_type"] == "INVOICE_LINE"
    )


def test_clean_chain_exported_from_postgres_conforms_to_shacl(pipeline, exported):
    """The real tables, not a hand-written example, satisfy the ontology's shapes."""
    from p2p_recon.db import connect

    row = _invoice_for(pipeline, "S01")
    with connect() as connection:
        nodes = invoice_chain_nodes(connection, row["source_system"], row["document_number"])
    result = validate_graph(document_closure(exported, nodes))
    assert result.conforms, result.messages


@pytest.mark.parametrize("scenario", ["S03", "S07", "S14"])
def test_chain_with_a_valid_reason_code_conforms(pipeline, exported, scenario):
    """A real exception whose reason is a SKOS concept is valid data. The reason check once rejected every real code,
    because the vocabulary was not in the graph its SPARQL queried."""
    from p2p_recon.db import connect

    row = next(
        r
        for r in pipeline.expected
        if r["scenario_id"] == scenario
        and r["document_type"] == "INVOICE_LINE"
        and r["expected_reason_codes"]
    )
    with connect() as connection:
        nodes = invoice_chain_nodes(connection, row["source_system"], row["document_number"])
    result = validate_graph(document_closure(exported, nodes))
    assert result.conforms, result.messages


@pytest.mark.parametrize(
    ("scenario", "fragment"), [("S12", "Segregation of duties"), ("S11", "Approval limit exceeded")]
)
def test_shacl_independently_finds_what_sql_controls_find(pipeline, exported, scenario, fragment):
    """Two independent implementations of a control, in SHACL over RDF and in SQL, agree on the same documents."""
    from p2p_recon.db import connect

    row = _invoice_for(pipeline, scenario)
    with connect() as connection:
        nodes = invoice_chain_nodes(connection, row["source_system"], row["document_number"])
    result = validate_graph(document_closure(exported, nodes))
    assert not result.conforms
    assert any(fragment in message for _, message in result.messages), result.messages
