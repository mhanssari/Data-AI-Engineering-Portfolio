"""Chunk the generated documents, embed them locally and store them in docs.

Chunking follows the documents' own structure rather than a fixed window. A clause, a section or a single policy
parameter is the unit a person would cite, and each is short enough to embed whole, so there is no overlap to tune.
Near-identical contract clauses would otherwise be indistinguishable, so every chunk is embedded and indexed with a
context header naming its document, supplier and section.

Reloading is idempotent and embeds only text that changed. Chunks are derived data: a document removed from the
manifest loses its chunks.
"""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from fastembed import TextEmbedding
from pgvector.psycopg import register_vector

from p2p_recon.config import CASE_DIR
from p2p_recon.db import connect, pipeline_run

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSION = 384
MODEL_CACHE = CASE_DIR / "data" / "models"
BULLET = re.compile(r"^- \*\*(?P<code>[a-z0-9_]+)\*\*", re.MULTILINE)


@lru_cache
def embedder() -> TextEmbedding:
    return TextEmbedding(EMBEDDING_MODEL, cache_dir=str(MODEL_CACHE))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def chunk_markdown(document_id: str, text: str) -> list[dict]:
    """Split on level-two headings; text before the first heading is the overview; each policy bullet stands alone."""
    title = text.splitlines()[0].removeprefix("# ").strip()
    parts = re.split(r"^## ", text.split("\n", 1)[1], flags=re.MULTILINE)
    sections = [("Overview", parts[0].strip())] + [
        (part.split("\n", 1)[0].strip(), part.split("\n", 1)[1].strip()) for part in parts[1:]
    ]
    chunks: list[dict] = []
    for section, body in sections:
        if not body:
            continue
        bullets = list(BULLET.finditer(body))
        if bullets:
            ends = [match.start() for match in bullets[1:]] + [len(body)]
            for match, end in zip(bullets, ends, strict=True):
                chunks.append((section, slug(match["code"]), body[match.start() : end].strip()))
        else:
            chunks.append((section, slug(section), body))
    return [
        {
            "chunk_id": f"{document_id}:{chunk_slug}",
            "chunk_index": index,
            "section": section,
            "title": title,
            "content": content,
        }
        for index, (section, chunk_slug, content) in enumerate(chunks)
    ]


def _context_header(entry: dict, chunk: dict) -> str:
    parts = [chunk["title"]]
    if entry["supplier_name"]:
        parts.append(f"Supplier {entry['supplier_name']} ({entry['source_supplier_id']})")
    if entry["company_code"] != "GROUP":
        parts.append(f"Company {entry['company_code']}")
    parts.append(chunk["section"])
    return " | ".join(parts)


def load_documents(documents_dir: Path) -> dict:
    manifest = json.loads((documents_dir / "manifest.json").read_text(encoding="utf-8"))
    with pipeline_run("load_documents", {"documents_dir": str(documents_dir)}) as run, connect() as conn:
        register_vector(conn)
        existing = {
            row["chunk_id"]: row["embedded_sha256"]
            for row in conn.execute(
                "select chunk_id, embedded_sha256 from docs.chunk where embedding_model = %s",
                (EMBEDDING_MODEL,),
            )
        }
        chunks_to_embed: list[dict] = []
        all_chunk_ids: set[str] = set()
        for entry in manifest:
            text = (documents_dir / entry["file"]).read_text(encoding="utf-8")
            chunks = chunk_markdown(entry["document_id"], text)
            conn.execute(
                """
                insert into docs.document (document_id, file_name, doc_type, title, company_code, source_system,
                    source_supplier_id, supplier_name, contract_id, invoice_number, valid_from, valid_to, access_tags,
                    content_sha256, last_run_id)
                values (%(document_id)s, %(file)s, %(doc_type)s, %(title)s, %(company_code)s, %(source_system)s,
                    %(source_supplier_id)s, %(supplier_name)s, %(contract_id)s, %(invoice_number)s, %(valid_from)s,
                    %(valid_to)s, %(access_tags)s, %(sha)s, %(run)s)
                on conflict (document_id) do update set
                    file_name = excluded.file_name, doc_type = excluded.doc_type, title = excluded.title,
                    company_code = excluded.company_code, source_system = excluded.source_system,
                    source_supplier_id = excluded.source_supplier_id, supplier_name = excluded.supplier_name,
                    contract_id = excluded.contract_id, invoice_number = excluded.invoice_number,
                    valid_from = excluded.valid_from, valid_to = excluded.valid_to, access_tags = excluded.access_tags,
                    content_sha256 = excluded.content_sha256, last_run_id = excluded.last_run_id
                """,
                {
                    **entry,
                    "title": chunks[0]["title"],
                    "sha": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "run": run.run_id,
                },
            )
            for chunk in chunks:
                header = _context_header(entry, chunk)
                embed_text = f"{header}\n{chunk['content']}"
                sha = hashlib.sha256(embed_text.encode("utf-8")).hexdigest()
                chunk.update(
                    document_id=entry["document_id"],
                    company_code=entry["company_code"],
                    access_tags=entry["access_tags"],
                    context_header=header,
                    embed_text=embed_text,
                    sha=sha,
                )
                all_chunk_ids.add(chunk["chunk_id"])
                if existing.get(chunk["chunk_id"]) == sha:
                    # Same text and model: keep the vector, but permissions may still have changed.
                    conn.execute(
                        "update docs.chunk set company_code = %s, access_tags = %s where chunk_id = %s",
                        (chunk["company_code"], chunk["access_tags"], chunk["chunk_id"]),
                    )
                else:
                    chunks_to_embed.append(chunk)

        if chunks_to_embed:
            vectors = list(embedder().passage_embed([c["embed_text"] for c in chunks_to_embed]))
            for chunk, vector in zip(chunks_to_embed, vectors, strict=True):
                conn.execute(
                    """
                    insert into docs.chunk (chunk_id, document_id, chunk_index, section, context_header, content,
                        company_code, access_tags, embedding, embedding_model, embedded_sha256)
                    values (%(chunk_id)s, %(document_id)s, %(chunk_index)s, %(section)s, %(context_header)s, %(content)s,
                        %(company_code)s, %(access_tags)s, %(vector)s, %(model)s, %(sha)s)
                    on conflict (chunk_id) do update set
                        chunk_index = excluded.chunk_index, section = excluded.section,
                        context_header = excluded.context_header, content = excluded.content,
                        company_code = excluded.company_code, access_tags = excluded.access_tags,
                        embedding = excluded.embedding, embedding_model = excluded.embedding_model,
                        embedded_sha256 = excluded.embedded_sha256
                    """,
                    {**chunk, "vector": vector, "model": EMBEDDING_MODEL},
                )

        ids = list(all_chunk_ids)
        removed_chunks = conn.execute(
            "delete from docs.chunk where not (chunk_id = any(%s))", (ids,)
        ).rowcount
        documents = [entry["document_id"] for entry in manifest]
        removed_documents = conn.execute(
            "delete from docs.document where not (document_id = any(%s))", (documents,)
        ).rowcount
        run.lineage(conn, "data/generated/documents", "docs.chunk", len(ids))
        conn.commit()
        run.counts = {
            "documents": len(manifest),
            "chunks": len(ids),
            "embedded": len(chunks_to_embed),
            "removed_chunks": removed_chunks,
            "removed_documents": removed_documents,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimension": EMBEDDING_DIMENSION,
        }
        return run.counts
