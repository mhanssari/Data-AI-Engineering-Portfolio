# 0004 Supplier entity resolution: method and thresholds

Status: accepted

Date: 30 October 2026

## Context

The same supplier appears in both ERPs under different identifiers. Duplicate detection, spend views and fraud checks depend on resolving them. A wrong merge is worse than a missed one: it can link an invoice to the wrong bank details or hide a fraud signal.

## Options

1. **Exact match only (VAT, bank).** Safe, but misses suppliers with no shared identifier.
2. **Deterministic rules plus a fuzzy score, with a steward band.** Explainable; every decision has a reason a person can read.
3. **Probabilistic linkage (for example Splink) or a trained model.** Better at large scale with weak identifiers; needs training data or careful calibration, and each decision is harder to explain to an auditor.

## Decision and rationale

Option 2, with [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz), which is fast, needs no service and has deterministic output. Evidence is applied strongest first:

| Evidence | Outcome | Why |
| --- | --- | --- |
| Same VAT number | Auto-merge | The only identifier an unrelated company cannot share |
| Different VAT numbers, combined score ≥ 75 | Steward, never auto | Sister companies are real: same name and address is not proof |
| Same bank account, name score ≥ 60 | Auto-merge | Corroborated by the name |
| Same bank account, unrelated name | Steward, stays apart | A fraud signal to investigate, not evidence of identity |
| Combined score ≥ 92 | Auto-merge | |
| Combined score 75–92 | Steward | |
| Otherwise | Nothing recorded | |

Combined score = 0.7 × name + 0.3 × address, after standardising case, punctuation, transliteration and legal suffixes. Name score averages `token_sort` and `token_set`, so an extra word ("Food") costs less than a different word.

The thresholds are **calibrated on this synthetic population only**: planted pairs land in their bands and no background pair is flagged. They are not evidence of how the method would perform on real supplier data, where they would need calibrating against a labelled sample.

Survivorship is deliberately thin: the newest record names the cluster and the first VAT number found labels it. Source records keep their own values, so bank details are never merged. Hence `p2p:resolvesTo` rather than `owl:sameAs`.

## Consequences and validation

- Steward decisions are append-only, override the rules and survive rebuilds.
- All pairs are compared, which is fine for 60 suppliers. At scale this needs blocking, for example on postcode or a name token.
- Merges are transitive. A=B and B=C merges all three even if A and C conflict on VAT. No such case is planted, and no check detects it yet. A production version should flag it.

Verified by tests: every planted pair reaches its expected method and decision; only planted pairs are flagged; golden ids are stable across rebuilds; a steward merge and a later rejection both take effect; S21 proves the value downstream, as only the golden record links the same invoice across ERPs.
