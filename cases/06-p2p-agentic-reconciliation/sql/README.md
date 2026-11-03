# Sql

- `migrations/NNN_*.sql`: tables with declared grain, applied once each, with a checksum so an edited file is refused. `001` schemas and `ops`, `002` `raw`, `003` `core`, `004` `mdm` and `recon`, `005` `docs` and `agent`, `006` roles, row-level security and the audit log, `007` the governance reviewer, its review notes and the admin view log, `008` `ops.raw_origin`, which gives a raw row's file, row and checksum without its values.
- `routines/*.sql`: views and functions, re-applied on every `p2p migrate`. `10_stg` staging views, `20_core_views` ontology-facing views, `30_recon` the matching engine and current-state views, `40_agent` grants for every role and the review queue, `50_admin` the reviewer's grants.
- `rules.yaml`: a plain-English description of each rule in `30_recon`, with its level, policy parameters, tables read and planted scenarios. The SQL is the rule; a test fails if the two disagree on which rules exist, or if the scenarios differ from what the generator plants.

Money is NUMERIC with currency. See [security](../docs/security.md).
