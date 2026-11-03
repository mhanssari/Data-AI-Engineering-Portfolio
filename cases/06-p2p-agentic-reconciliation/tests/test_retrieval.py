"""Retrieval returns citations, and permissions hold for every method. Allowed and denied examples for each kind of user."""

import pytest
import yaml

from p2p_recon import evaluation, graph, retrieval
from p2p_recon.config import EVALUATION_DIR

pytestmark = pytest.mark.integration

DE_SUPPLIER_QUERY = "What is the price tolerance for Jena Textil?"
GB_SUPPLIER_QUERY = "What is the price tolerance for Kelso Glass?"


@pytest.fixture(scope="module")
def db(graph_synced):
    with graph.driver() as driver:
        yield driver


@pytest.fixture
def users(conn):
    return {name: retrieval.load_user(conn, name) for name in ("ap_analyst_uk", "ap_analyst_de", "auditor")}


@pytest.mark.parametrize("method", retrieval.METHODS)
def test_clerk_never_receives_the_other_companys_chunks(conn, users, db, method):
    for query in (
        DE_SUPPLIER_QUERY,
        "Who countersigns the call when a supplier changes bank account?",
        "credit note",
    ):
        ids = retrieval.ranked_ids(
            conn, users["ap_analyst_uk"], query, method, db, ("SAP_LIKE", "5100000012")
        )
        companies = conn.execute(
            "select distinct company_code from docs.chunk where chunk_id = any(%s)", (ids,)
        ).fetchall()
        assert {c["company_code"] for c in companies} <= {"GB01", "GROUP"}


@pytest.mark.parametrize("method", retrieval.METHODS)
def test_each_company_finds_its_own_procedure_for_the_same_question(conn, users, db, method):
    question = "Who countersigns the call when a supplier changes bank account?"
    gb = retrieval.search(conn, users["ap_analyst_uk"], question, method, 5, db)
    de = retrieval.search(conn, users["ap_analyst_de"], question, method, 5, db)
    assert "chunk:procedure_GB01:verifying-a-bank-change" in [h.citation for h in gb]
    assert "chunk:procedure_DE01:verifying-a-bank-change" in [h.citation for h in de]


@pytest.mark.parametrize("method", retrieval.METHODS)
def test_audit_memo_is_visible_to_the_auditor_alone(conn, users, db, method):
    question = "What is internal audit sampling this year?"
    for name in ("ap_analyst_uk", "ap_analyst_de"):
        cited = {h.document_id for h in retrieval.search(conn, users[name], question, method, 20, db)}
        assert not {"audit_memo_GB01", "audit_memo_DE01"} & cited
    cited = {h.document_id for h in retrieval.search(conn, users["auditor"], question, method, 5, db)}
    assert {"audit_memo_GB01", "audit_memo_DE01"} & cited


def test_filtering_happens_before_ranking(conn, users):
    """Filtering after ranking would leave a GB01 clerk with few results for a query dominated by DE01 contracts."""
    query = "Altona Bergedorf Celle Detmold Emden Flensburg Goslar Hameln Itzehoe Jena Kassel Lingen GmbH tolerance"
    hits = retrieval.search(conn, users["ap_analyst_uk"], query, "vector", 20)
    assert len(hits) == 20
    assert all(h.company_code in ("GB01", "GROUP") for h in hits)


def test_group_policy_is_visible_to_everyone(conn, users):
    for user in users.values():
        hits = retrieval.search(conn, user, "price tolerance default purchase order price", "hybrid", 10)
        assert any(h.company_code == "GROUP" for h in hits)


def test_hits_carry_a_citation_and_their_source(conn, users):
    hit = retrieval.search(conn, users["ap_analyst_uk"], GB_SUPPLIER_QUERY, "vector", 1)[0]
    assert hit.citation == "chunk:contract_CT-2026-108_2026-01-01:clause-4-invoice-tolerance"
    assert "Kelso Glass" in hit.context_header
    assert "percent" in hit.content


def test_rrf_rewards_agreement_and_is_rank_based():
    assert retrieval.rrf([["a", "b"], ["b", "c"]])[0] == "b"
    assert retrieval.rrf([["a"], []]) == ["a"]
    assert retrieval.rrf([]) == []


def test_rrf_scores_match_the_formula():
    # b: 1/62 + 1/61 beats a: 1/61 alone; c: 1/62 alone comes last, and ties break on the id.
    assert retrieval.rrf([["a", "b"], ["b", "c"]]) == ["b", "a", "c"]


def test_blank_keyword_query_returns_nothing_rather_than_failing(conn, users):
    assert retrieval.keyword_ids(conn, users["ap_analyst_uk"], "the and of") == []


def test_unknown_user_and_method_are_refused(conn, users):
    with pytest.raises(ValueError):
        retrieval.load_user(conn, "nobody")
    with pytest.raises(ValueError):
        retrieval.ranked_ids(conn, users["ap_analyst_uk"], "tolerance", "psychic")


# ---------------------------------------------------------------- graph


def test_neighbourhood_of_another_companys_invoice_looks_absent(users, db):
    assert retrieval.invoice_neighbourhood(db, users["ap_analyst_uk"], "ORACLE_LIKE", "90002") is None
    assert (
        retrieval.invoice_neighbourhood(db, users["ap_analyst_de"], "ORACLE_LIKE", "90002")["invoice_number"]
        == "BEG-2026-0001"
    )
    assert (
        retrieval.invoice_neighbourhood(db, users["ap_analyst_de"], "ORACLE_LIKE", "no-such-invoice") is None
    )


def test_golden_supplier_does_not_lead_into_the_other_erp(users, db):
    """S21: Brightwell exists in both ERPs under one golden record. A clerk learns that a sibling exists, not who it is."""
    clerk = retrieval.invoice_neighbourhood(db, users["ap_analyst_de"], "ORACLE_LIKE", "90012")
    assert clerk["visible_siblings"] == []
    assert clerk["hidden_siblings"] == 1
    auditor = retrieval.invoice_neighbourhood(db, users["auditor"], "ORACLE_LIKE", "90012")
    assert auditor["visible_siblings"] == ["0000100001"]
    assert auditor["hidden_siblings"] == 0


def test_graph_links_reach_only_permitted_documents(conn, users, db):
    neighbourhood = retrieval.invoice_neighbourhood(db, users["auditor"], "ORACLE_LIKE", "90012")
    for name, allowed in (("ap_analyst_de", {"DE01"}), ("auditor", {"GB01", "DE01"})):
        documents = retrieval.linked_document_ids(conn, users[name], neighbourhood)
        companies = conn.execute(
            "select distinct company_code from docs.document where document_id = any(%s)", (documents,)
        ).fetchall()
        assert {c["company_code"] for c in companies} <= allowed


def test_link_lookup_refuses_keys_from_another_company(conn, users):
    """The lookup filters by itself: a neighbourhood naming GB01 records gives a DE01 clerk nothing, and an auditor the lot."""
    gb01_keys = {
        "source_system": "SAP_LIKE",
        "source_supplier_id": "0000100018",
        "visible_siblings": [],
        "contract_ids": ["CT-2026-001"],
        "invoice_number": "LTL-2026-0001",
    }
    assert retrieval.linked_document_ids(conn, users["ap_analyst_de"], gb01_keys) == []
    assert "contract_CT-2026-001_2026-01-01" in retrieval.linked_document_ids(
        conn, users["auditor"], gb01_keys
    )


def test_graph_connects_a_question_that_names_no_supplier(conn, users, db):
    """Only the invoice links this question to the supplier's contract, so hybrid search alone cannot rank it first."""
    question = "What are the payment terms with this supplier?"
    expected = "contract_CT-2026-108_2026-01-01:clause-5-payment-terms"
    with_graph = retrieval.ranked_ids(
        conn, users["ap_analyst_uk"], question, "hybrid_graph", db, ("SAP_LIKE", "5100000012")
    )
    without = retrieval.ranked_ids(
        conn, users["ap_analyst_uk"], question, "hybrid", db, ("SAP_LIKE", "5100000012")
    )
    assert with_graph.index(expected) < 5
    assert expected not in without[:5]


def test_graph_context_does_not_change_an_unanchored_search(conn, users, db):
    query = "price tolerance"
    assert retrieval.ranked_ids(
        conn, users["ap_analyst_uk"], query, "hybrid_graph", db
    ) == retrieval.ranked_ids(conn, users["ap_analyst_uk"], query, "hybrid", db)


# ---------------------------------------------------------------- evaluation


def test_metrics_on_a_hand_worked_example():
    rankings = {"q1": ["a", "b"], "q2": ["x", "y", "z"], "q3": ["m"], "q4": ["p", "q", "r", "s", "t", "u"]}
    expected = {"q1": ["a"], "q2": ["z"], "q3": ["absent"], "q4": ["u", "q"]}
    scores = evaluation.score_rankings(rankings, expected)
    assert scores["recall@1"] == 1 / 4
    assert scores["recall@3"] == 3 / 4
    assert scores["recall@5"] == 3 / 4
    assert scores["mrr"] == pytest.approx((1 + 1 / 3 + 0 + 1 / 2) / 4)


def test_every_label_names_a_chunk_that_exists(loaded_documents, conn):
    questions = yaml.safe_load((EVALUATION_DIR / "retrieval_questions.yaml").read_text(encoding="utf-8"))[
        "questions"
    ]
    assert len(questions) >= 30
    known = {r["chunk_id"] for r in conn.execute("select chunk_id from docs.chunk")}
    assert {c for q in questions for c in q["expected"]} <= known


def test_stale_label_stops_the_evaluation(graph_synced, tmp_path):
    path = tmp_path / "questions.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "questions": [
                    {
                        "id": "x",
                        "kind": "rule",
                        "user": "ap_analyst_uk",
                        "question": "q",
                        "expected": ["no:such-chunk"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no:such-chunk"):
        evaluation.evaluate_retrieval(path, report_dir=None)


def test_graph_lifts_anchored_questions_in_the_evaluation(graph_synced):
    result = evaluation.evaluate_retrieval(report_dir=None)
    anchored = {name: m["by_kind"]["anchored"]["mrr"] for name, m in result["methods"].items()}
    assert anchored["hybrid_graph"] > anchored["hybrid"]
    assert anchored["hybrid_graph"] > anchored["vector"]
