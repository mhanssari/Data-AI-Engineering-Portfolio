# Sql

- `migrations/NNN_*.sql`: tables with declared grain, applied once each, with a checksum so an edited file is refused. `001` schemas and `ops`, `002` `raw`, `003` `core`, `004` `mdm` and `recon`, `005` `docs` and `agent`, `006` roles, row-level security and the audit log, `007` the governance reviewer, its review notes and the admin view log.
- `routines/*.sql`: views and functions, re-applied on every `p2p migrate`. `10_stg` staging views, `20_core_views` ontology-facing views, `30_recon` the matching engine and current-state views, `40_agent` grants for every role and the review queue, `50_admin` the reviewer's grants.

Money is NUMERIC with currency. See [security](../docs/security.md).
