# Architecture decisions

| ID | Decision | Status |
| --- | --- | --- |
| [0001](0001-graph-store.md) | Neo4j as a rebuildable graph projection; PostgreSQL stays authoritative | Accepted |
| [0002](0002-postgres-pgvector.md) | PostgreSQL with pgvector for structured data and embeddings | Accepted |
| [0003](0003-deterministic-matching.md) | Deterministic SQL matching; agents explain and propose only | Accepted |
| [0004](0004-supplier-resolution.md) | Supplier resolution: rules, rapidfuzz score and a steward band | Accepted |
| [0005](0005-recompute-and-snapshots.md) | Full recompute with a snapshot per run | Accepted |
| [0006](0006-incremental-ingestion.md) | Append-only raw, watermark with lookback, upsert if newer | Accepted |

Copy [the template](TEMPLATE.md) to a numbered descriptive file when a material choice is made. Record context, alternatives, chosen approach, rationale and consequences. Status values: proposed, accepted or superseded. A rejected alternative is useful evidence when its trade-off is explained.

Expected further decisions: agent orchestration (LangGraph or plain state machine), embedding model and dimension.
