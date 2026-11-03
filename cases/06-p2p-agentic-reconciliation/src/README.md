# Src

The `p2p_recon` package, with the `p2p` command as its entry point. Business logic stays testable; money and matching stay in SQL.

| Module | Responsibility |
| --- | --- |
| `cli` | One command per step, plus `run-all` and `status` |
| `config`, `db` | Settings from `.env`; connections; run bookkeeping that survives a failed step |
| `migrate` | Forward-only migrations with checksums; repeatable views and functions |
| `generate` | Seeded ERP extracts, documents, expected outcomes |
| `ingest` | Land a batch in `raw`, checked against its manifest |
| `conform` | Watermark, latest version, quarantine, upsert if newer |
| `mdm` | Supplier entity resolution |
| `recon` | Calls `recon.run_matching` in SQL; no logic of its own |
| `ontology` | SHACL validation, the mapping, RDF export |
| `graph` | Neo4j rebuild and reconciliation; Cypher library runner |
| `dq` | Checks and reconciliations |

Planned for milestone 2: retrieval, agents, evaluation.
