# Architecture decisions

| ID | Decision | Status |
| --- | --- | --- |
| [0001](0001-graph-store.md) | Neo4j as a rebuildable graph projection; PostgreSQL stays authoritative | Accepted |
| [0002](0002-postgres-pgvector.md) | PostgreSQL with pgvector for structured data and embeddings | Accepted |
| [0003](0003-deterministic-matching.md) | Deterministic SQL matching; agents explain and propose only | Accepted |
| [0004](0004-supplier-resolution.md) | Supplier resolution: rules, rapidfuzz score and a steward band | Accepted |
| [0005](0005-recompute-and-snapshots.md) | Full recompute with a snapshot per run | Accepted |
| [0006](0006-incremental-ingestion.md) | Append-only raw, watermark with lookback, upsert if newer | Accepted |
| [0007](0007-retrieval.md) | Structure-based chunks, local embeddings, exact scan, rank fusion, graph as a third list | Accepted |
| [0008](0008-agent-orchestration.md) | LangGraph, one model-calling agent, and enforcement by role, guard and append-only trail | Accepted |
| [0009](0009-security-model.md) | Agents act with a person's database role, under row-level security | Accepted |
| [0010](0010-admin-review-dashboard.md) | Governance review: a read-only role that decides nothing, and a separate admin app | Accepted |

Copy [the template](TEMPLATE.md) to a numbered descriptive file when a material choice is made. Record context, alternatives, chosen approach, rationale and consequences. Status values: proposed, accepted or superseded. A rejected alternative is useful evidence when its trade-off is explained.
