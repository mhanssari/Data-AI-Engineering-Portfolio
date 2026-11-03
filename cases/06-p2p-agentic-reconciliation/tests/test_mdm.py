"""Supplier entity resolution: planted pairs reach the expected decision, and steward decisions stick."""

import pytest

from p2p_recon import mdm
from p2p_recon.mdm import standardise_name, standardise_vat


def _pair_rows(pipeline):
    return [r for r in pipeline.expected if r["document_type"] == "SUPPLIER_PAIR"]


def _keys(row):
    a, b = row["document_number"].split("|")
    return tuple(a.split(":")), tuple(b.split(":"))


def _gid(conn, key):
    return conn.execute(
        "select supplier_gid from mdm.supplier_xref where source_system = %s and source_supplier_id = %s", key
    ).fetchone()["supplier_gid"]


def test_planted_pairs_reach_the_expected_decision(pipeline, conn):
    for row in _pair_rows(pipeline):
        a, b = _keys(row)
        same_golden = _gid(conn, a) == _gid(conn, b)
        low, high = sorted((a, b))
        candidate = conn.execute(
            "select method, decision from mdm.supplier_match_candidate where a_source_system = %s and a_source_supplier_id = %s "
            "and b_source_system = %s and b_source_supplier_id = %s",
            (*low, *high),
        ).fetchone()
        note = row["description"]
        if row["expected_status"] == "Merged":
            assert same_golden, note
            assert candidate["decision"] == "AUTO_MERGE", note
            assert candidate["method"] == row["expected_reason_codes"], note
        elif row["expected_status"] == "StewardReview":
            assert not same_golden, f"must stay apart until a steward decides: {note}"
            assert candidate["decision"] == "STEWARD_REVIEW", note
            assert candidate["method"] == row["expected_reason_codes"], note
        else:
            assert not same_golden, note
            assert candidate is None, note


def test_only_planted_pairs_are_flagged(pipeline, conn):
    """Thresholds that also flag background suppliers would swamp stewards with false positives."""
    candidates = conn.execute("select count(*) as n from mdm.supplier_match_candidate").fetchone()["n"]
    flagged = sum(1 for r in _pair_rows(pipeline) if r["expected_status"] != "NotMerged")
    assert candidates == flagged


def test_golden_and_crosswalk_counts(pipeline, conn):
    row = conn.execute("""
        select (select count(*) from core.supplier) as suppliers,
               (select count(*) from mdm.supplier_xref) as xref,
               (select count(*) from mdm.supplier_golden) as golden,
               (select sum(member_count) from mdm.supplier_golden) as members
    """).fetchone()
    merged = sum(1 for r in _pair_rows(pipeline) if r["expected_status"] == "Merged")
    assert row["suppliers"] == row["xref"] == row["members"] == 60
    assert row["golden"] == 60 - merged


def test_golden_ids_are_stable_across_rebuilds(pipeline, conn):
    before = conn.execute(
        "select source_system, source_supplier_id, supplier_gid from mdm.supplier_xref order by 1, 2"
    ).fetchall()
    conn.rollback()
    mdm.resolve_suppliers()
    after = conn.execute(
        "select source_system, source_supplier_id, supplier_gid from mdm.supplier_xref order by 1, 2"
    ).fetchall()
    assert before == after


def test_steward_decision_overrides_and_survives_rebuild(pipeline, conn):
    row = next(
        r
        for r in _pair_rows(pipeline)
        if r["expected_status"] == "StewardReview" and r["expected_reason_codes"] == "FUZZY"
    )
    a, b = _keys(row)
    try:
        mdm.record_steward_decision(a, b, "MERGE", "test steward")
        mdm.resolve_suppliers()
        conn.rollback()
        assert _gid(conn, a) == _gid(conn, b)
        method = conn.execute(
            "select match_method from mdm.supplier_xref where source_system = %s and source_supplier_id = %s",
            a,
        ).fetchone()["match_method"]
        assert method == "STEWARD"
        conn.rollback()
        mdm.record_steward_decision(
            b, a, "REJECT", "test steward"
        )  # latest decision wins, in either argument order
        mdm.resolve_suppliers()
        assert _gid(conn, a) != _gid(conn, b)
    finally:
        conn.rollback()
        conn.execute("delete from mdm.steward_decision")
        conn.commit()
        mdm.resolve_suppliers()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Brightwell Packaging Ltd", "BRIGHTWELL PACKAGING"),
        ("BRIGHTWELL PACKAGING LIMITED", "BRIGHTWELL PACKAGING"),
        ("Müller & Söhne GmbH", "MUELLER SOEHNE"),
    ],
)
def test_standardise_name(value, expected):
    assert standardise_name(value) == expected


def test_standardise_vat():
    assert standardise_vat("GB 100 200 301") == standardise_vat("gb100200301") == "GB100200301"
    assert standardise_vat("") is None
