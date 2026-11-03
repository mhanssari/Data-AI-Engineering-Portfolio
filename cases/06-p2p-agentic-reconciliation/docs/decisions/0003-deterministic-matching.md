# 0003 Deterministic matching and money logic; agents explain and propose

Status: accepted

Date: 30 October 2026

## Context

Invoice matching decides whether money can be released. Finance and audit need results that are repeatable, explainable and testable. Language models can reason over evidence but are not deterministic calculators.

## Options

1. **LLM performs matching** from retrieved records. Flexible; not repeatable; hard to audit; arithmetic risk.
2. **Deterministic SQL matching and controls; agents investigate and propose.** Repeatable and testable; agents add explanation, evidence gathering and next-step proposals.

## Decision and rationale

Option 2. All matching, tolerances, controls and amounts are computed in SQL with exact decimals. Agents call these functions as tools, gather evidence through graph and document retrieval, and write proposals that a finance user must approve. No agent can post, pay or change core data.

## Consequences and validation

Agent value is measured on explanation quality and investigation speed, not on matching itself. Verified at milestone 1: all matching tests pass with no LLM in the system, because none exists yet. Verified at milestone 2, with an offline provider and a hostile one, not yet a live model: the agent evaluation shows zero false auto-approvals, and every proposal carries citations to rows or chunks. Explanation quality has not been measured.
