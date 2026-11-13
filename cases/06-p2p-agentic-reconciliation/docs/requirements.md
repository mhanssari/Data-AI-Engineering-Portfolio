# Requirements

Status: requirements 1–11 are implemented and tested locally. Requirement 10 is evaluated with an offline provider only, not a live model. Security, contracts, lineage, the review page and CI were added at milestone 3; the workflow has [CI passed on GitHub](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/41709833238).

## Stakeholders and decisions

| Persona | Decision supported | Needs from this case |
| --- | --- | --- |
| Accounts payable analyst | Release, hold or query an invoice | Match status per invoice line, exception reason, evidence and a proposed action |
| Financial controller | Accept month-end position and control effectiveness | Goods received not invoiced (GRNI), exception ageing, control breaches, audit trail |
| Procurement lead | Fix upstream causes | Maverick spend, retrospective POs, split requisitions, supplier duplicates |
| Auditor (read-only) | Test controls | Immutable log of agent proposals, human decisions and source evidence |
| AI engineer in a delivery pod | Build agents on trusted data | Stable data products, data contracts, retrieval services with citations |

## Functional requirements

1. Ingest two ERP-shaped sources in full and incremental batches; land unchanged with run metadata; reruns are idempotent.
2. Conform both sources to one canonical procure-to-pay model aligned to the ontology.
3. Resolve suppliers across sources into golden records with a cross-reference and steward-review band.
4. Apply controls: approval present, approver within limit, requester is not approver, no split requisitions under a limit, no retrospective PO.
5. Match invoices: three-way (PO, GRN, invoice) for goods and two-way (PO, invoice) for services, at line level, with price and quantity tolerances taken from policy and supplier contracts.
6. Detect duplicate invoices, invoices without a PO, currency mismatches and recent supplier bank-detail changes.
7. Calculate GRNI at a stated cut-off date.
8. Project the canonical model to a knowledge graph and answer chain and relationship questions.
9. Chunk and embed contracts, policy and supplier correspondence; provide permission-aware hybrid and graph retrieval with citations.
10. Run agents that investigate exceptions using only tools, and write proposals for human review.
11. Record lineage, data-quality results, agent traces and human decisions.

## Non-functional requirements

- Deterministic money and matching logic in SQL; NUMERIC with currency; no floating point for money.
- Every table has a declared grain and key; every metric has a single definition in metrics.md.
- Works offline by default (no paid API needed for tests or demo).
- Windows 11, Docker Desktop and PowerShell-compatible commands.
- Secrets only in a local `.env`; `.env.example` holds names and dummy values.

## Acceptance scenarios

See [scenarios](scenarios.md) for the planted cases and expected outcomes. In addition: reruns create no duplicates; a late correction changes only affected results; control totals reconcile by company and currency; ontology passes SHACL; agents cannot write to core tables; cross-company access is denied; zero false auto-approvals in the agent evaluation.
