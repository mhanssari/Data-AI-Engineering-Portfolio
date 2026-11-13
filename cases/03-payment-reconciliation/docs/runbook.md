# Operational runbook

Status: local batch, streaming intake/live, checkpoint recovery and bounded late backfill are implemented and tested.

- [Case README](../README.md): prerequisites, case configuration and direct CLI execution.
- [Shared Airflow run guide](../orchestration/README.md): optional platform configuration, preparation, backfill, rerun verification and shutdown. Run and task logs are visible in the local Airflow UI; `ops.run` records reconciliation executions, including failed checks.
- [Streaming run guide](../streaming/README.md): shared broker startup, optional image, separate database/checkpoints, reference snapshot, status, restart, controlled repair, state checks and current-clock measurement.
- [Core rebuild decision](decisions/0004-core-rebuilt-as-of-arrival.md): held events, illegal transitions and unknown-merchant reprocessing.
- [Reconciliation decision](decisions/0005-reconciliation-runs-and-grace.md): grace, exceptions, invariant failures and atomic publication.

A rejected arrival must be corrected at source and loaded again; inspect `ops.quarantine` for row-level rejection reasons. Preserve original source files for audit. A failed invariant publishes no replacement results; diagnose the logged check, correct the cause, then clear the failed Airflow task or repeat the corresponding CLI command. Never run a CLI writer concurrently with this DAG against the same database.

For streaming, inspect `stream-status` for the selected live generation before trusting the view. Its consistency flag covers recorded frontiers, not the advancing broker head. Inspect `stream.quarantine` and `stream.live_conflict` for immutable identity conflicts; do not overwrite canonical events or reset checkpoints to bypass them. Restart an ordinary stopped reader with its existing dataset/source bounds, topic epoch and checkpoint path. Database and checkpoint lineage must agree. Use the documented paused-reader plan/backfill flow for late missing events; it requires a complete retained prefix within its stated limits. Broker-history loss and arbitrary checkpoint corruption have no automatic recovery guarantee.

Credentials belong in ignored local configuration. Stop shared infrastructure only when other cases do not need it; case cleanup must not delete shared volumes. Streaming restart and injected before/after-commit recovery are recorded in [recovery evidence](../evidence/stream-recovery-run.md). No cloud operations have been validated.
