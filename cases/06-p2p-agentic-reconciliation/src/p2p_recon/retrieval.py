"""Permission-aware retrieval: full-text, vector, reciprocal-rank fusion and graph neighbourhood.

The acting user's permissions sit in the WHERE clause of each ranking query, so a chunk the user cannot see is never
ranked, and cannot crowd out or reveal anything by its position. Results carry chunk ids for citation.

The vector search is an exact scan. An approximate index filters after its scan, which can return too few permitted
rows, and a few hundred chunks do not need one (decision 0007).
"""

from dataclasses import dataclass, field

import psycopg
from neo4j import READ_ACCESS, Driver
from pgvector.psycopg import register_vector

from p2p_recon.documents import embedder
from p2p_recon.graph import QUERIES_DIR

METHODS = ("keyword", "vector", "hybrid", "hybrid_graph")
RRF_K = 60  # The constant from the original reciprocal-rank fusion paper; it is not tuned here.
CANDIDATES = 50

# Evaluated before any ranking. Group documents are open to every company; access tags must also overlap.
PERMITTED = """
    (c.company_code = 'GROUP' or c.company_code = any(%(companies)s)) and c.access_tags && %(tags)s
"""


@dataclass(frozen=True)
class ActingUser:
    user_name: str
    company_codes: list[str]
    access_tags: list[str]
    source_systems: list[str]


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    doc_type: str
    section: str
    context_header: str
    content: str
    company_code: str
    methods: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        return f"chunk:{self.chunk_id}"


def load_user(conn: psycopg.Connection, user_name: str) -> ActingUser:
    row = conn.execute(
        """
        select u.user_name, u.company_codes, u.access_tags,
               array(select le.source_system from core.legal_entity le where le.company_code = any(u.company_codes)) as systems
        from agent.acting_user u where u.user_name = %s
        """,
        (user_name,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown acting user: {user_name}")
    return ActingUser(row["user_name"], row["company_codes"], row["access_tags"], row["systems"])


def _params(user: ActingUser, **extra: object) -> dict:
    return {"companies": user.company_codes, "tags": user.access_tags, "limit": CANDIDATES, **extra}


def keyword_ids(
    conn: psycopg.Connection, user: ActingUser, query: str, within: list[str] | None = None
) -> list[str]:
    # plainto_tsquery requires every word, which a natural-language question rarely satisfies, so terms are ORed
    # and ts_rank does the ordering.
    scope = "and c.document_id = any(%(within)s)" if within is not None else ""
    rows = conn.execute(
        f"""
        with q as (select replace(plainto_tsquery('english', %(query)s)::text, '&', '|') as text_query)
        select c.chunk_id
        from docs.chunk c, q
        where {PERMITTED} {scope} and q.text_query <> '' and c.search_vector @@ q.text_query::tsquery
        order by ts_rank(c.search_vector, q.text_query::tsquery) desc, c.chunk_id
        limit %(limit)s
        """,
        _params(user, query=query, within=within),
    ).fetchall()
    return [row["chunk_id"] for row in rows]


def vector_ids(
    conn: psycopg.Connection, user: ActingUser, query: str, within: list[str] | None = None
) -> list[str]:
    register_vector(conn)
    vector = next(iter(embedder().query_embed([query])))
    scope = "and c.document_id = any(%(within)s)" if within is not None else ""
    rows = conn.execute(
        f"""
        select c.chunk_id
        from docs.chunk c
        where {PERMITTED} {scope}
        order by c.embedding <=> %(vector)s, c.chunk_id
        limit %(limit)s
        """,
        _params(user, vector=vector, within=within),
    ).fetchall()
    return [row["chunk_id"] for row in rows]


def rrf(rankings: list[list[str]]) -> list[str]:
    """Fuse by rank, not score: full-text ranks and cosine distances are not on comparable scales."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + position)
    return sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))


def invoice_neighbourhood(db: Driver, user: ActingUser, source_system: str, invoice_id: str) -> dict | None:
    """None when the invoice is absent or belongs to a company the user cannot see: the two look the same on purpose."""
    query = (QUERIES_DIR / "invoice_neighbourhood.cypher").read_text(encoding="utf-8")
    with db.session(default_access_mode=READ_ACCESS) as session:
        record = session.run(
            query,
            source_system=source_system,
            invoice_id=invoice_id,
            companies=user.company_codes,
            systems=user.source_systems,
        ).single()
    return record.data() if record else None


def linked_document_ids(conn: psycopg.Connection, user: ActingUser, neighbourhood: dict) -> list[str]:
    systems = [neighbourhood["source_system"]]
    suppliers = [neighbourhood["source_supplier_id"], *neighbourhood["visible_siblings"]]
    rows = conn.execute(
        """
        select d.document_id
        from docs.document d
        where d.company_code = any(%(companies)s)
          and (d.contract_id = any(%(contracts)s)
               or d.invoice_number = %(invoice)s
               or (d.source_system = any(%(systems)s) and d.source_supplier_id = any(%(suppliers)s)))
        order by d.document_id
        """,
        {
            "companies": user.company_codes,
            "contracts": neighbourhood["contract_ids"],
            "invoice": neighbourhood["invoice_number"],
            "systems": systems + user.source_systems,
            "suppliers": suppliers,
        },
    ).fetchall()
    return [row["document_id"] for row in rows]


def ranked_ids(
    conn: psycopg.Connection,
    user: ActingUser,
    query: str,
    method: str = "hybrid",
    db: Driver | None = None,
    invoice: tuple[str, str] | None = None,
    within: list[str] | None = None,
) -> list[str]:
    if method == "keyword":
        return keyword_ids(conn, user, query, within)
    if method == "vector":
        return vector_ids(conn, user, query, within)
    if method not in METHODS:
        raise ValueError(f"Unknown retrieval method: {method}")
    rankings = [keyword_ids(conn, user, query, within), vector_ids(conn, user, query, within)]
    if method == "hybrid_graph" and invoice and db:
        neighbourhood = invoice_neighbourhood(db, user, *invoice)
        documents = linked_document_ids(conn, user, neighbourhood) if neighbourhood else []
        if documents:
            rankings.append(vector_ids(conn, user, query, within=documents))
    return rrf(rankings)


def rule_document_ids(conn: psycopg.Connection) -> list[str]:
    return [
        row["document_id"]
        for row in conn.execute(
            "select document_id from docs.document where doc_type in ('policy', 'procedure')"
        )
    ]


def search(
    conn: psycopg.Connection,
    user: ActingUser,
    query: str,
    method: str = "hybrid",
    k: int = 5,
    db: Driver | None = None,
    invoice: tuple[str, str] | None = None,
    within: list[str] | None = None,
) -> list[Hit]:
    top = ranked_ids(conn, user, query, method, db, invoice, within)[:k]
    # Permissions apply again here, so that fetching text is never the one path without them.
    rows = conn.execute(
        f"""
        select c.chunk_id, c.document_id, d.doc_type, c.section, c.context_header, c.content, c.company_code
        from docs.chunk c join docs.document d using (document_id)
        where c.chunk_id = any(%(ids)s) and {PERMITTED}
        """,
        _params(user, ids=top),
    ).fetchall()
    by_id = {row["chunk_id"]: row for row in rows}
    return [Hit(**by_id[chunk_id], methods=[method]) for chunk_id in top if chunk_id in by_id]
