# 0004 Core rebuilt whole from raw, as of an arrival date

Status: accepted

Date: 12 November 2026

## Context

Core holds distinct events, payment state, illegal transitions and distinct settlement lines. It must give the same result when a day is rerun (A7, invariant 8), including after later files have landed, and late or out-of-order events must change state correctly (A2). Raw is append-only and records each file's arrival date.

## Options

1. **Incremental merge per arrival.** Apply only new raw rows to core. Fast at scale, but rerunning an earlier day after later loads needs undo logic, and out-of-order events need the state walk to restart from the affected sequence.
2. **Full rebuild as of an arrival date.** Truncate core and derive it from every raw row whose file had arrived by that date, in one transaction.

## Decision and rationale

Option 2. Core as of arrival A is a pure function of what had arrived by A, so a rerun of any day is identical by construction, and a late event simply joins the next rebuild. At this case's scale (177,000 events, 49,000 settlement lines) a full build took 5.8 seconds in PostgreSQL on this laptop (Docker, 12 November 2026).

How it is built (`sql/core/build.sql`):
- **Events:** the first delivery of each `event_id` is kept and the number of deliveries recorded.
- **Payment state:** a recursive walk from a `new` state at sequence 0 steps to sequence + 1 through `core.allowed_transition`, so it stops at the first gap. Events beyond a gap are *held*: neither applied nor illegal, until the missing event arrives. An event with no allowed transition, or a refund larger than what remains captured, is recorded in `core.illegal_transition` and the state carries over.
- **Sequence contract:** two different events with one sequence number in one payment stop the build (unique constraint) rather than be ordered by guesswork.
- **Settlement lines:** the latest delivery of each line id wins, so a corrected re-issue of a file supersedes the original. An identical re-send never reaches raw.
- **Unknown merchants:** events whose merchant is not yet in reference data go to `ops.quarantine` (stage `core`) and are read again on every build; the entry is marked reprocessed once the merchant arrives.

## Consequences and validation

- Core reflects one as-of date at a time (`core.build`). Reconciliation results, which must be kept per run, are written by the reconciliation stage, not by core.
- Quarantine is an operations log, not part of the rebuild: a row is quarantined at most once per stage and keeps its first reprocessing time, even if an earlier day is rebuilt afterwards.
- Core has no foreign keys. With them, per-row constraint triggers took about 11 of the 14 seconds that one insert needed; integrity is asserted by `test_core_is_referentially_whole` instead.
- Connections run in autocommit, so every unit of work is an explicit transaction and a build never nests inside an implicit one.
- Tests: rebuilding 10 October before and after loading everything gives identical table digests; every payment's state equals an independent Python replay of the generator's events.
- At scale this becomes option 1, partitioned by payment and settlement date; see "What I would do differently at scale" in the requirements.
