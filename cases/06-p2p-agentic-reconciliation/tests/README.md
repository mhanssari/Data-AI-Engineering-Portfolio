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
| `test_documents.py` | Chunks follow structure with stable ids; contract clauses state what `core.contract` holds; added contracts change no match; a reload embeds only what changed and applies permission changes |
| `test_retrieval.py` (marked `integration`) | Allowed and denied examples for two companies and an auditor, on every method; filtering before ranking; the golden supplier does not lead into the other ERP; the graph connects questions that name no supplier; metrics on a hand-worked example |
| `test_agents.py` (marked `integration`) | The database refuses every forbidden write and read; the guard's rules; a held line's full trail; denials; redaction across companies; core and recon unchanged after a hostile run; outage, step limit, token limit and invented tools; review-queue lifecycle; request shape and parsing for live providers, against mocks; the false-approval metric moves when the guard is removed |
| `test_security.py` (marked `integration`) | Through the real login and roles: allowed and denied rows on every table and view, catalogue checks that nothing added later slips past, bank numbers, on-behalf-of, decision rights, audit log, append-only trail |
| `test_contracts.py` (marked `integration`) | Contracts are complete; the database honours them; live drift and quality breaches are detected; freshness on an injected clock; each breaking change fails without a major version |
| `test_review_and_lineage.py` (marked `integration`) | A proposal traces to source files and documents; a late correction shows the corrected row; the page's queue, citations, graph and decisions for each role, through Streamlit's `AppTest` |

Live providers are tested against mocks only, so nothing here calls a real model.
