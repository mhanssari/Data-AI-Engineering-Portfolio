# 0007 Where metric logic lives: dbt for rules, DAX for aggregation

Status: accepted

Date: 11 November 2026

## Context

Every metric in [metrics.md](../metrics.md) needs some logic: which purchase qualifies, when a member is eligible, when a cohort is mature, which reward status to believe. The same rule written twice, once in SQL and once in DAX, will drift. But some arithmetic has to happen in the report, because a ratio cannot be added up across slices.

## Options

1. **Everything in DAX.** One place for the report. Rules over windows and sequences (90-day activity, first purchase, cohort maturity) are slow and hard to test in DAX. Nothing outside Power BI could use them.
2. **Everything in dbt, the report only displays.** Testable and reusable. But a precomputed ratio is wrong as soon as the report filters or groups differently.
3. **Rules and row-level flags in dbt; aggregation and ratios in DAX.** dbt decides `is_eligible`, `is_active`, `is_mature`, `is_resolved`, the qualifying purchase and the store version in force, and tests them. DAX counts, sums and divides those flags in whatever filter context the report applies.

## Decision and rationale

Option 3. Each business rule is written once, in SQL, with unit and invariant tests. Each DAX measure is a count, sum or `DIVIDE` over dbt's flags, and the measures that need judgement say so in their description:
- **Snapshot measures** take the last month in the filter, so a member count is never summed over months.
- **Blank when immature:** the repeat-purchase rate is blank, never 0%, while a cohort is immature.
- **Version-aware trading days:** a store is counted only while one of its versions in the filter was open.

## Consequences and validation

- `bi/reconcile.ps1` evaluates 34 checks in Power BI and compares each with independent SQL over the same files, including blanks and RLS. The first run found one defect (a failed-test count that showed blank instead of 0), which was fixed.
- The report cannot redefine a metric without changing dbt; a reviewer reads the rule in one SQL file.
- Another BI tool would need the DAX aggregation rewritten, but not the rules. A semantic layer would remove even that; it is listed under what would change at scale.
