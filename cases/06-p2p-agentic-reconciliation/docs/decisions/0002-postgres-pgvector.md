# 0002 PostgreSQL with pgvector as one store for structured data and embeddings

Status: accepted

Date: 30 October 2026

## Context

Agents need structured facts (POs, receipts, invoices) and unstructured evidence (contracts, policy, emails). Retrieval must respect the same company-level permissions as the structured data, and every answer must cite its source.

## Options

1. **Separate vector database** (for example a managed vector service). Strong scaling; a second security model to keep aligned; extra cost and service.
2. **PostgreSQL with the pgvector extension.** Vectors sit next to the data and metadata; the same roles and row-level security apply; full-text search is built in for hybrid retrieval. Less suited to very large vector volumes.

## Decision and rationale

Option 2. The case's volumes are small, and the main risk is governance rather than scale. One store means one permission model, simple joins between chunks and business keys, and hybrid search (full-text plus vector) in one query.

## Consequences and validation

The extension runs on `pgvector/pgvector:0.8.7-pg18-trixie`. Embedding dimension and the choice of no vector index are in [decision 0007](0007-retrieval.md). Validated: a user from one company cannot retrieve the other company's chunks; the retrieval evaluation compares vector-only and hybrid. At much larger volumes a dedicated vector store could be reconsidered, with permissions synchronised from the system of record.
