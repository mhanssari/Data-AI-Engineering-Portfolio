# Procure-to-pay ontology

Status: v0.2.0, version recorded in `p2p.ttl` and `mapping.yaml`. Used by the Neo4j projection, the RDF export and the data-quality vocabulary check.

## Files

| File | Purpose |
| --- | --- |
| `p2p.ttl` | OWL classes and properties: legal entity, supplier and golden supplier, bank account, employee, cost centre, GL account, item, contract, requisition, approval, purchase order and lines, goods receipt and lines, supplier invoice and lines, credit note, payment proposal, match result, exception |
| `vocab.ttl` | SKOS concept schemes: match status, exception reasons (grouped into matching, control and risk), document types with English/German labels, and a small glossary (three-way match, two-way match, GRNI) |
| `shapes.ttl` | SHACL shapes for structural rules, plus SPARQL constraints for segregation of duties, approval limit and valid exception reasons |
| `mapping.yaml` | Mapping from ontology classes and properties to PostgreSQL tables/columns and Neo4j labels/relationship types |
| `examples/valid-chain.ttl` | A compliant requisition → approval → PO → GRN → invoice chain, and one supplier resolved across both ERPs |
| `examples/invalid-chain.ttl` | Deliberate defects with expected violations |

## How the ontology is used

1. **Shared language:** tables in `core` are named after these classes; reason codes in `recon` come from the SKOS schemes.
2. **Validation:** tests export real chains from PostgreSQL to RDF through the mapping and validate them with SHACL. A clean chain conforms. S11 and S12 are flagged by the SHACL constraints as well as by the SQL controls, so two independent implementations agree.
3. **Graph projection:** `mapping.yaml` drives the Neo4j loader, so node labels, relationship types and property names follow the ontology. The mapping is an allowlist, so bank account numbers cannot leak into the graph by a column being added.
4. **Controlled codes:** a data-quality check confirms that every status and reason written to `recon` is a concept in the vocabulary.
5. **Agent grounding (not built):** agents read reason codes from SQL, where the vocabulary constrains them, but the prompt does not yet include their definitions. Proposed actions are not yet in the vocabulary.

Design note: `p2p:resolvesTo` links a source supplier to its golden record instead of `owl:sameAs`, because `owl:sameAs` would merge every statement about the two source records, including conflicting bank details that must stay separate for fraud checks.

Design note: rdfs:domain is an inference rule, not a constraint. Validating real data exposed that a domain of `Supplier` on `vatNumber` classified every golden supplier as a source supplier, so properties shared by several classes have no domain.

Defined but not yet projected: `CreditNote` (a supplier invoice with `creditsInvoice`, typed through inference), `JournalEntry` (the accrual sits in `recon.accrual_journal_line`), `PaymentProposal` (the `recon.current_payment_readiness` view; agent proposals are in `agent.proposal` and not mapped to the ontology). Oracle's supplier sites are flattened onto `Supplier`: only the primary pay site is modelled.

## Versioning

A change to a class, property or shape bumps `owl:versionInfo` in `p2p.ttl` and `version` in `mapping.yaml`, which move together. v0.2.0 added journal entry, new reason codes (`UnmatchedCreditNote`, `MissingApproval`, `MissingRequisition`) and the properties the mapping needs; it removed the unused `quantityTolerancePct`.

## Validation

`tests/test_ontology.py` runs on every `uv run pytest`, with versions locked in `uv.lock`: all Turtle parses; `valid-chain.ttl` conforms; `invalid-chain.ttl` raises exactly its five planted violations; every mapped name is defined in the ontology; every mapped table and column exists in the database; real exported chains behave as above, including chains whose exceptions carry valid reason codes. To validate the examples and a fixed sample of 25 real invoice chains, and record one result per chain in `ops.dq_result` (pipeline step `validate_ontology`, which `run-all` also runs):

```powershell
uv run p2p validate-ontology            # --sample 50 for a larger sample
```

The sample is ordered by a hash of the invoice key, so it is the same on every run while the data is unchanged. A chain that breaks a control fails, as SQL holds it. The admin Ontology page shows the last recorded result.

Validation defect found and fixed: the vocabulary was passed to pySHACL only as the ontology graph, but a SPARQL constraint queries the data graph alone, so the reason-code check rejected every real reason code. The examples did not show it, because the valid example has no exception. `validate_graph` now adds the vocabulary to the data it validates.
