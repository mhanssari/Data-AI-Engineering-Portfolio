# Operational runbook

Status: no running system yet.

When implemented, document prerequisites, dependency installation, configuration, sample-data setup, execution order, validation commands, refresh/backfill, failure diagnosis, quarantine/reprocessing, restart, shutdown and resource cleanup.

Every command must be verified in the selected environment. Include how to identify a run and inspect logs/counts/freshness. Do not add destructive cleanup commands without explicit scope and safeguards. Credentials belong in local configuration or a secret manager, not this document.
