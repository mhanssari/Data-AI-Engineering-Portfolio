# Operational runbook

Status: local batch system implemented; streaming remains planned.

- [Case README](../README.md): prerequisites, case configuration and direct CLI execution.
- [Shared Airflow run guide](../orchestration/README.md): optional platform configuration, preparation, backfill, rerun verification and shutdown. Run and task logs are visible in the local Airflow UI; `ops.run` records reconciliation executions, including failed checks.
- [Core rebuild decision](decisions/0004-core-rebuilt-as-of-arrival.md): held events, illegal transitions and unknown-merchant reprocessing.
- [Reconciliation decision](decisions/0005-reconciliation-runs-and-grace.md): grace, exceptions, invariant failures and atomic publication.

A rejected arrival must be corrected at source and loaded again; inspect `ops.quarantine` for row-level rejection reasons. Preserve original source files for audit. A failed invariant publishes no replacement results; diagnose the logged check, correct the cause, then clear the failed Airflow task or repeat the corresponding CLI command. Never run a CLI writer concurrently with this DAG against the same database.

Credentials belong in ignored local configuration. Stop shared infrastructure only when other cases do not need it; case cleanup must not delete shared volumes. No cloud operations or streaming restart procedures have been validated.
