# Tests

Run with `uv run pytest` from the case folder; containers must be up. Results: [evidence](../evidence/milestone-1-run.md). Tests create their own `p2p_test` database and take both batches through the pipeline once per session.

| File | Proves |
| --- | --- |
| `test_scenarios.py` | Every planted scenario reaches its exact status and reasons after each batch; no exception outside them; every status and reason code is exercised; GRNI and the accrual; generator determinism |
| `test_incremental.py` | Redelivery and rerun change nothing; a late correction changes only the affected results; stale rows, deletes, quarantine and lineage; corrupt, truncated and drifted deliveries fail atomically; a row the watermark misses fails the reconciliation |
| `test_mdm.py` | Planted supplier pairs reach the expected method and decision; only they are flagged; stable ids; steward decisions override and persist |
| `test_dq.py` | Reconciliations pass, cover both companies and all currencies, can fail when core is altered, and state quarantine as a reconciling item |
| `test_ontology.py` | Files parse; examples behave; mapping agrees with ontology and database; real chains conform to SHACL; SHACL and SQL find the same breaches |
| `test_graph.py` (marked `integration`) | Graph counts reconcile; rebuild is idempotent; no bank numbers; every Cypher query answers correctly; chain gaps agree with SQL |

Not yet tested, because not yet built: row-level security, agents, retrieval.
