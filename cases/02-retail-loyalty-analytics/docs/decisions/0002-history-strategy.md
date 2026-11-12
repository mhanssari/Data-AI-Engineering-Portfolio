# 0002 History: rebuild store versions from raw; snapshot members

Status: accepted

Date: 11 November 2026

## Context

Two different history questions exist. Store operations need sales attributed to the store's format and region **in force on the transaction date**. Finance and audit need to know **what the warehouse reported before a correction** to a member. The sources differ too: stores arrive as dated versions, members as latest state that a correction overwrites.

## Options

1. **dbt snapshots for both.** Idiomatic and familiar. For stores, a snapshot only records changes when it runs, so versions that took effect before the first run, or between runs, get the wrong dates, and a clean clone cannot rebuild them.
2. **Rebuild both from append-only raw.** Fully reproducible. For members, raw holds every delivery, so "what we knew" can be derived, but that rebuilds by hand what a snapshot does natively.
3. **Rebuild stores from raw; snapshot members.** Each question uses the tool that fits its source.

## Decision and rationale

Option 3. `dim_store` is a type 2 dimension built from the dated versions in raw on every run, so it is exact and reproducible. `snp_member` is a dbt snapshot with the `timestamp` strategy on `updated_at`, run after each batch, because members arrive as latest state and a snapshot is the standard way to keep history the source discards.

## Consequences and validation

- `dim_store` passes invariants 5 and 6 (no overlaps or gaps, one version per sales line) on every build.
- `snp_member` depends on run order. A clean clone rebuilds it only by replaying batch 1, snapshot, batch 2, snapshot; the CLI and the Airflow backfill both do this. A run that skipped batch 1 would lose the pre-correction state, and the README says so.
- `dim_member` stays type 1, so reports use corrected values; the snapshot is for audit, not for joining facts.
- Acceptance A12 checks that the corrected member has two versions after batch 2.
