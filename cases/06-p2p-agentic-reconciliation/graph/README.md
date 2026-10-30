# Graph

Neo4j projection of `core`, `mdm` and current `recon`, built from [`../ontology/mapping.yaml`](../ontology/mapping.yaml) by `uv run p2p graph-sync`. PostgreSQL remains authoritative; the graph is dropped and rebuilt on each sync, with node and edge counts reconciled and recorded in `ops.dq_result`. Trade-offs: [decision 0001](../docs/decisions/0001-graph-store.md).

Each label has a uniqueness constraint on a `key` property, which folds in the composite business key, because Community Edition has no node-key constraint. Money is held as strings: use SQL for arithmetic.

## Query library (`queries/`)

Run with `uv run p2p graph-query <name> --param key=value`, or paste into Neo4j Browser at <http://127.0.0.1:7474>.

| Query | Question | Parameters |
| --- | --- | --- |
| `invoice_chain` | The whole document chain and status for an invoice | `source_system`, `invoice_id` |
| `chain_gaps` | Invoices with no PO or receipt, POs with no requisition, requisitions with no approval | none |
| `approval_path` | Approvals compared with each approver's limit then and today | `source_system`, `pr_number` |
| `supplier_duplicates` | Suppliers held under several source ids | none |
| `golden_invoices` | All invoices for a golden supplier across source ids | `supplier_gid` |
