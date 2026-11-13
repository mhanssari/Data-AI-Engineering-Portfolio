# Final local MVP review

**Local MVP validated.** Review source: `0de8ae53b37b5225dfc839b9beb69b92fde93e2a`, branch `case-03-payments`, 12 November 2026. All eleven application/reproducibility stages passed. Handoff finalised on 13 November 2026. Finalisation changes only documentation and CI cleanup, leaving the audited application, SQL and dependency lock unchanged.

## Reproducibility boundary

Created a separate local Git clone with `--no-hardlinks`, checked out the exact committed measurement source detached and verified an empty Git status. No case `.env`, generated source files or host virtual environment were copied. Windows initially rejected a long path in another case; enabling `core.longpaths` only in the disposable clone completed checkout without changing repository files.

Both batch and optional streaming images were built from this checkout, with ordinary Docker layer caches available. Audit image tags were `payment-reconciliation-final-0de8ae5:batch` and `:stream`. A local, ignored Compose override selected these tags and removed PostgreSQL's host-port binding with `ports: !reset []`. Project `case03-final-0de8ae5` had no pre-existing containers or volumes; its PostgreSQL and checkpoint volumes were fresh. It used dummy local database settings, with no original credentials or database copied.

Kafka remained the existing shared portfolio resource. The audit started it from its stopped state, used only the helpers' unique diagnostic topics and preserved its primary topic, volume and original demonstration data. Airflow stayed stopped. This validates a case checkout against the documented shared-service prerequisite, not a new cloud deployment or an independent broker installation.

The host used only standard-library Python and Docker. The committed helpers were unchanged: the local driver supplied the isolated Compose project/image override through `check_intake.COMPOSE` before importing the remaining helpers. Its platform commands continued to address the original shared Kafka stack. This changes resource placement, not event fixtures, processing logic or acceptance assertions.

## Checks

- Built both images from committed source; CLI smoke checks passed. The batch environment had no PySpark, confluent-kafka or Java runtime; the optional measurement helper imported and displayed its CLI.
- Ruff lint and formatting passed from the clean batch image (92 Python files).
- Full test suite with `PAYRECON_REQUIRE_DB=1`: **99 passed, zero failures/errors/skips, 346.589 seconds** in the saved local JUnit report. The Compose command including startup/report writing took 350.69 seconds.
- Real intake check passed in 110.39 seconds: poison/late retention, same-checkpoint restart and repeated publication; final 16 deliveries, three canonical events and eight quarantine rows.
- Real live check passed in 356.64 seconds: held gaps, illegal transition/expiry eligibility, poison/future-clock rejection, checkpoint restart, duplicate safety, reference refresh and observed watermark exclusion. Final 19 deliveries, 14 intake events, 13 admitted live events; the one excluded event remains in intake and correctly makes readiness false pending controlled repair.
- Real recovery check passed in 325.41 seconds: abrupt exits before and after database commit, rollback/replay, unchanged first-publication metadata, one recovered late authorisation, independent batch state/minute parity, idempotent repair, unchanged re-delivery and same-checkpoint continuation.
- Continuous A12 check passed in 266.83 seconds: eight cohorts, retained keys 48/96/144/144/144/144/144/144, cumulative removals 0/0/0/48/96/144/192/240. Intake retained 480 deliveries/384 canonical events; live retained all 384 events and 192 payments. Idle, duplicate and old re-delivery controls passed, recorded frontiers were consistent, and the same query/checkpoint resumed without changing business fingerprints.
- Git status in the audited checkout remained empty after all checks. No test-generated file entered the source commit.

The separate [database CI run](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43279448364) for this source passed **99 tests in 116.005 seconds**, with no failures/errors/skips. The [streaming CI run](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43279448369) passed intake/live/recovery/state, uploads and cleanup from its fresh Ubuntu checkout. Downloaded JUnit, recovery and state artifacts were inspected: both crash points, one repaired event, six matching payment states/eight applied minute groups, unchanged resend, repeated state eviction, retained history and checkpoint resume all agree with the declared fixture.

Test/check durations are diagnostics for these environments, not a performance comparison. The three [current-clock measurement runs](stream-measurement-run.md) remain the latency/throughput evidence; this final review does not silently substitute historical event age or integration runtime for those measurements.

## Cleanup finding and resolution

After all application checks passed, the one-off audit wrapper exited 1 on its final volume-absence assertion. Compose `down -v` without the optional streaming profile removed PostgreSQL storage but left the unattached audit checkpoint volume. Inspected its exact audit-project labels and confirmed no container referenced it, then removed only that volume. This was a cleanup finding, not a failed transformation/recovery assertion.

Updated the streaming workflow to include `--profile streaming` during disposable-runner cleanup and assert that neither case nor broker project volumes remain. A focused local regression created a uniquely labelled empty checkpoint volume, ran the corrected profile-aware `down -v`, and verified its removal without starting services. The temporary review wrapper was corrected as well; the application checks were not rerun for this cleanup-only change. The final push's streaming workflow validates cleanup on its actual runner.

Independent verification then re-read the clean JUnit/recovery/state artifacts, source commit and clean Git status, and confirmed zero audit/test-project containers or volumes. The two unique audit image tags were removed. Shared Kafka was returned to its prior stopped state; original PostgreSQL stayed healthy and Airflow stayed stopped. The retained demonstration still had 16 deliveries, three canonical events, eight quarantine rows and canonical digest `1f27ca689a124e180370e8a6b0cea38b`; primary-topic offsets remained zero in all three partitions.

The [machine-readable review summary](final-review-summary.json) records all stage timings and the resolved initial cleanup failure. Full local log, JUnit and streaming reports remain ignored under `evidence/ci/final-review-0de8ae5/`; the clean checkout's generated reports are under its case `evidence/ci/` directory.

Closing documentation checks resolved 240 relative Markdown links, including the local handoff notes; Git whitespace checks passed. Application Python, SQL and dependency files remained unchanged from the audited commit.

## Scope and handoff

The local MVP consists of deterministic batch reconciliation, shared Airflow late-file recovery, shared Kafka with case-owned intake/live processing, operational SQL views, checkpoint/duplicate safety, controlled late repair, finite observed state eviction and bounded current-clock measurement. Authoritative metric and recovery choices remain in the [metric catalogue](../docs/metrics.md) and [decisions](../docs/decisions/README.md).

Full-month accelerated streaming replay, concurrent-reader capacity, long-duration/peak-memory or checkpoint-disk studies, database archival, general broker-loss recovery, a separate BI report and cloud/Databricks deployment are optional extensions. No required local-MVP step or business decision remains. Use the [case guide](../README.md) and [operational runbook](../docs/runbook.md) for a new session; the confirmed shared-infrastructure, decline-rate and lateness choices remain unchanged.
