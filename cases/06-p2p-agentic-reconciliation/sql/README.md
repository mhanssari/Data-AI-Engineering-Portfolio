# Sql

- `migrations/NNN_*.sql`: tables with declared grain, applied once each, with a checksum so an edited file is refused. `001` schemas and `ops`, `002` `raw`, `003` `core`, `004` `mdm` and `recon`, `005` `docs` and `agent`.
- `routines/*.sql`: views and functions, re-applied on every `p2p migrate`. `10_stg` staging views, `20_core_views` ontology-facing views, `30_recon` the matching engine and current-state views, `40_agent` the agents' grants and the review queue.

Money is NUMERIC with currency. The `p2p_agent` role exists; row-level security and masked views are planned for milestone 3.
