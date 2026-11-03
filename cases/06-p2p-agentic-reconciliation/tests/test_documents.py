"""Chunking, reload behaviour, and the rule that a document can never disagree with core."""

import json
import re
import shutil

import pytest

from p2p_recon import documents
from p2p_recon.documents import EMBEDDING_DIMENSION, chunk_markdown

CONTRACT = """# Price agreement X (synthetic)

Between A and B.

## Clause 3: Pricing

Item at 1.00.

## Clause 4: Invoice tolerance

Up to 1 percent.
"""

POLICY = """# Policy (synthetic)

## Tolerances

- **first_rule** (1 percent): One.
- **second_rule** (2 days): Two.

## Segregation of duties

Nobody approves their own request.
"""


def test_chunks_follow_clauses_with_stable_ids():
    chunks = chunk_markdown("contract_x", CONTRACT)
    assert [c["chunk_id"] for c in chunks] == [
        "contract_x:overview",
        "contract_x:clause-3-pricing",
        "contract_x:clause-4-invoice-tolerance",
    ]
    assert chunks[2]["content"] == "Up to 1 percent."


def test_each_policy_parameter_is_its_own_chunk():
    chunks = chunk_markdown("policy", POLICY)
    assert [c["chunk_id"] for c in chunks] == [
        "policy:first-rule",
        "policy:second-rule",
        "policy:segregation-of-duties",
    ]
    assert "Two" not in chunks[0]["content"]


def test_corpus_loaded_with_expected_shape(loaded_documents, conn):
    assert loaded_documents["documents"] > 60
    assert loaded_documents["chunks"] > 200
    row = conn.execute(
        "select min(vector_dims(embedding)) as low, max(vector_dims(embedding)) as high, count(*) filter (where embedding_model <> %s) as other "
        "from docs.chunk",
        (documents.EMBEDDING_MODEL,),
    ).fetchone()
    assert (row["low"], row["high"], row["other"]) == (EMBEDDING_DIMENSION, EMBEDDING_DIMENSION, 0)
    # A chunk with no permissions would be invisible; one with no company would be unfiltered.
    assert (
        conn.execute(
            "select count(*) as n from docs.chunk where cardinality(access_tags) = 0 or company_code = ''"
        ).fetchone()["n"]
        == 0
    )


def test_contract_documents_state_what_core_holds(pipeline, loaded_documents, conn):
    """Invariant: a clause quoted to a user carries the same tolerance as the row SQL matches against."""
    rows = conn.execute(
        """
        select ch.content, c.price_tolerance_pct
        from docs.chunk ch
        join docs.document d using (document_id)
        join core.contract c on c.contract_id = d.contract_id and c.valid_from = d.valid_from
        where ch.section = 'Clause 4: Invoice tolerance'
        """
    ).fetchall()
    assert len(rows) >= 26
    for row in rows:
        stated = re.search(r"up to ([\d.]+) percent", row["content"]).group(1)
        assert float(stated) == float(row["price_tolerance_pct"])


def test_added_contracts_govern_no_purchase_order_line(pipeline, conn):
    """The corpus must not change any match result: only the S23 contract may be referenced by a PO line."""
    referenced = conn.execute(
        "select distinct contract_id from core.po_line where contract_id is not null"
    ).fetchall()
    assert [r["contract_id"] for r in referenced] == ["CT-2026-001"]
    assert conn.execute("select count(distinct contract_id) as n from core.contract").fetchone()["n"] == 25


def test_reload_embeds_nothing_new(loaded_documents, pipeline):
    again = documents.load_documents(pipeline.data_dir / "documents")
    assert again["embedded"] == 0
    assert again["chunks"] == loaded_documents["chunks"]
    assert again["removed_chunks"] == 0


@pytest.fixture
def working_copy(pipeline, loaded_documents, tmp_path):
    target = tmp_path / "documents"
    shutil.copytree(pipeline.data_dir / "documents", target)
    yield target
    documents.load_documents(pipeline.data_dir / "documents")  # restore for the rest of the session


def test_a_changed_clause_is_the_only_thing_re_embedded(working_copy, conn):
    path = working_copy / "procedure_GB01.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("Telephone the supplier", "Ring the supplier"),
        encoding="utf-8",
    )
    result = documents.load_documents(working_copy)
    assert result["embedded"] == 1
    conn.rollback()
    content = conn.execute(
        "select content from docs.chunk where chunk_id = 'procedure_GB01:verifying-a-bank-change'"
    ).fetchone()
    assert content["content"].startswith("Ring the supplier")


def test_a_removed_document_loses_its_chunks(working_copy, conn):
    manifest_path = working_copy / "manifest.json"
    manifest = [
        m
        for m in json.loads(manifest_path.read_text(encoding="utf-8"))
        if m["document_id"] != "audit_memo_DE01"
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = documents.load_documents(working_copy)
    assert result["removed_documents"] == 1
    assert result["removed_chunks"] == 2
    conn.rollback()
    assert (
        conn.execute("select count(*) as n from docs.chunk where document_id = 'audit_memo_DE01'").fetchone()[
            "n"
        ]
        == 0
    )


def test_a_tightened_permission_reaches_chunks_without_re_embedding(working_copy, conn):
    manifest_path = working_copy / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest:
        if entry["document_id"] == "procedure_GB01":
            entry["access_tags"] = ["audit"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = documents.load_documents(working_copy)
    assert result["embedded"] == 0
    conn.rollback()
    tags = conn.execute(
        "select distinct access_tags from docs.chunk where document_id = 'procedure_GB01'"
    ).fetchall()
    assert [t["access_tags"] for t in tags] == [["audit"]]
