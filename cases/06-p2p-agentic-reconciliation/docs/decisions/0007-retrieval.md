# 0007 Chunking, embedding and retrieval

Status: accepted

Date: 2 November 2026

## Context

Agents need to cite contracts, policy and correspondence. The corpus has awkward properties: the contracts are almost word for word the same, so the supplier name decides which clause is right; a clerk's question often names an invoice and no supplier; and one company must not read another's documents. Everything has to run locally with no paid service.

The first six generated documents could not support a meaningful comparison, because any method would find the right one. The corpus was enlarged first (84 documents, 238 chunks), with contract terms added as real rows in `core.contract`, so a document can never contradict SQL. No purchase order line references the added contracts, so no match result changed; 43 of the 45 structured files stayed byte-identical (the contract file and its manifest changed).

## Options

**Chunking.** Fixed-size windows with overlap are the common default, and split clauses arbitrarily. Structure-based chunks follow what a person would cite.

**Embedding.** A paid API, a larger local model, or a small local model. `BAAI/bge-small-en-v1.5` through fastembed needs no key and no GPU, is a 64 MB download, and loaded this corpus in 29 seconds, including that download.

**Vector search.** HNSW or IVFFlat, or an exact scan. Approximate indexes in pgvector filter after the index scan, so a permission filter can leave too few rows unless iterative scanning is tuned. An exact scan is always correct, and takes tens of milliseconds at this size.

**Fusion.** Weighted score combination needs comparable scores and tuning. Reciprocal-rank fusion uses ranks only and needs neither.

**Graph retrieval.** Project documents into Neo4j, or use the graph to find which documents relate to an invoice and rank them in PostgreSQL. The second keeps one store for text and one place for permissions.

## Decision and rationale

- **One chunk per clause, section or policy parameter, with a stable id** such as `contract_CT-2026-108_2026-01-01:clause-4-invoice-tolerance`. No overlap: each unit stands alone and the longest is 385 characters. Stable ids let labels and stored citations survive a rebuild.
- **A context header on every chunk** (document, supplier, company, section), embedded and indexed with its text. Without it, near-identical clauses have near-identical vectors.
- **`bge-small-en-v1.5`, 384 dimensions, exact scan.** The model and dimension are stored with each vector, and the column type enforces the dimension. A changed model re-embeds on the next load.
- **Full-text terms joined with OR**, ranked by `ts_rank`, because `plainto_tsquery` requires every word and a natural question rarely has them all.
- **Reciprocal-rank fusion with k = 60**, the paper's constant, untuned. Tuning it on 36 questions would only fit the labels.
- **Permissions in the WHERE clause of every ranking query:** company (or `GROUP`) and access tags. A forbidden chunk is never ranked, and so cannot crowd out or reveal anything. This is application-level filtering until row-level security arrives in milestone 3.
- **Graph retrieval as a third ranked list.** A read-only Cypher query finds the invoice's supplier, contracts and related records, filtered by company in the query because Community Edition has no fine-grained security. Documents linked to those keys are then ranked by vector similarity and fused in.

## Consequences and validation

Measured in [the retrieval evaluation](../../evidence/retrieval-evaluation.md). The results do not flatter the design, and they are kept as they came out:

- **Vector search alone beat hybrid on this question set.** Fusion helps where keywords win (emails quoting distinctive words) and hurts where they do not (supplier and rule questions, where ORed terms match many near-identical clauses). A plain "hybrid is better" claim would not be supported here.
- **The graph's value is specific:** questions about "this invoice" that name no supplier. There it lifts recall@5 sharply, because nothing else connects the question to the document.
- The evaluation is 36 questions written by the corpus's author, so differences of a question or two mean little. There is no held-out set, and retrieval was deliberately not tuned against these labels.

The English model is a real limit: German-language documents would need a multilingual model, and the corpus is English throughout. At much larger volumes an exact scan stops being viable, and HNSW with iterative scans or partitioning by company would need its recall measured under the permission filter.
