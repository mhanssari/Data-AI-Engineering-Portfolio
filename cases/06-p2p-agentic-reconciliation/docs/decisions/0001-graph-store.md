# 0001 Graph store for the procure-to-pay knowledge graph

Status: accepted

Date: 30 October 2026

## Context

Agents need to follow relationships quickly: invoice → PO lines → goods receipts → requisition → approvals → supplier golden record, and across ERPs through the crosswalk. The relational model already holds these links. The question is where relationship-heavy queries run.

## Options

| | Recursive SQL or fixed joins in PostgreSQL | Apache AGE inside PostgreSQL | Neo4j Community as a projection |
| --- | --- | --- | --- |
| Extra service | None | None, but an extension, which limits managed hosting | One more container and a sync step |
| Query language | SQL; fixed-depth chains are fine, variable-length patterns get awkward | openCypher, with Cypher-in-SQL syntax | Native Cypher, the most complete tooling |
| Consistency | Always current | Same transaction as the data | Stale until the next sync |
| Security | The same roles and row-level security | Shares the database, but policies do not reach the graph's own storage naturally | A separate access model; Community Edition has no fine-grained security |
| Ecosystem | Universal | Small | Largest in this field; Browser, libraries, GraphRAG examples |
| Fit for this data | Enough for every fixed-depth check here | Enough | More than is strictly needed |

## Decision and rationale

Neo4j as a projection rebuilt from PostgreSQL, which stays authoritative.

An honest statement of the reasons: for what this case does today, **SQL alone would be sufficient**, and it would be the right answer for a team that needed only these chain checks. The graph has no calculation in it and no control depends on it. Neo4j is chosen because it is the established tool for knowledge-graph work, because this case is meant to demonstrate it hands-on, and because milestone 2's graph retrieval benefits from variable-length patterns. The demonstration value is a portfolio reason as much as an engineering one, and it is stated rather than dressed up as necessity.

The cost is accepted and contained: one sync step, no second source of truth, no correctness dependency.

## Consequences and validation

- Rebuild rather than sync changes: simple and idempotent, and fast at this size. It would not suit a graph of hundreds of millions of nodes, where change capture would be needed.
- Staleness is real: the graph reflects the last `graph-sync`. Anything acting on it must treat PostgreSQL as the answer.
- Money is stored as strings in the graph, because Neo4j has no exact decimal type. `approval_path` uses `toFloat` for display only.
- Community Edition has no node-key constraint, so composite keys are folded into one `key` property with a uniqueness constraint. It also has no row-level security, which must be handled in the retrieval layer in milestone 2.
- Bank account numbers are not projected: `mapping.yaml` is an allowlist.

Verified by tests: node and edge counts reconcile with `core` per label and relationship type; rebuild is idempotent; `chain_gaps` gives the same answer in Cypher and in SQL (fixed joins rather than recursive SQL, since the chain depth is fixed).
