# Operational runbook

Verified on Windows 11 with Docker Desktop, in PowerShell, on 30 October 2026. Run everything from the case folder. Agents and embeddings are milestone 2.

## Prerequisites

Docker Desktop and [uv](https://docs.astral.sh/uv/). If `uv` was just installed with winget, open a new terminal so that it is on the PATH.

## Start

```powershell
Copy-Item .env.example .env
docker compose -f infra/docker-compose.yml --env-file .env up -d --wait
uv sync
```

## Steps

`uv run p2p run-all` does all of these in order. Each runs alone too, and each is safe to repeat.

| Command | What it does | Repeat behaviour |
| --- | --- | --- |
| `uv run p2p migrate` | Applies migrations once each, then re-applies views and functions | Stops if an applied migration file was edited |
| `uv run p2p generate` | Writes `data/generated` and `data/samples/expected_outcomes.csv`. `--seed` to vary | Same seed, same bytes |
| `uv run p2p ingest batch_001` | Lands a batch in `raw`; then `batch_002` | A file already landed is skipped |
| `uv run p2p conform` | Moves changed rows to `core`; retries quarantine | Writes nothing if nothing changed |
| `uv run p2p resolve-suppliers` | Rebuilds golden records, crosswalk and steward queue | Stable ids |
| `uv run p2p match` | Controls, matching, GRNI. `--cutoff 2026-09-30` | New snapshot, identical results |
| `uv run p2p dq` | Checks and reconciliations; exit 1 on failure | New results row per run |
| `uv run p2p graph-sync` | Rebuilds Neo4j; exit 1 if counts differ | Full rebuild |
| `uv run p2p graph-query <name>` | Runs a query from `graph/queries`; `--param key=value` | Read-only |
| `uv run p2p validate-ontology` | Validates the ontology examples against SHACL | Read-only |
| `uv run p2p status` | Summary of current results | Read-only |

## Common tasks

**See why an invoice is held:**

```powershell
uv run p2p graph-query invoice_chain --param source_system=ORACLE_LIKE --param invoice_id=<id>
```

Or in SQL: `recon.current_exception` holds the reason and evidence, `recon.current_match_result` the variances. Scenario document numbers are in `data/samples/expected_outcomes.csv`.

**Work the steward queue.** List pairs:

```powershell
docker exec p2p-case06-postgres-1 psql -U p2p_admin -d p2p -c "select * from mdm.supplier_match_candidate where decision = 'STEWARD_REVIEW'"
```

Decide, then rebuild and rematch:

```powershell
uv run p2p steward-decide SAP_LIKE:<id> ORACLE_LIKE:<id> MERGE --decided-by "<name>"
uv run p2p resolve-suppliers
uv run p2p match
```

## When something fails

| Symptom | Meaning | Action |
| --- | --- | --- |
| `ingest` fails: checksum, row count or header | The delivery does not match its manifest or raw table. The whole batch rolled back; `ops.pipeline_run` shows the error | Obtain a good delivery and rerun. Nothing needs cleaning up |
| `conform` fails on a cast | A malformed value in a source file. The run rolled back | Fix at source and redeliver. This is a known limit: such rows are not quarantined |
| `open_quarantine` warning | Rows waiting for a parent, listed in `ops.quarantine` with reasons | Usually clears when the parent arrives. Chase the source if it does not |
| `row_count.*` fails | A source key is in neither core nor quarantine. Most often a row older than the 24-hour lookback | Widen `ops.watermark.lookback` for that entity or set its `high_watermark` back, then run `conform` |
| `control_total.*` fails | Core differs from raw, for example after a manual change | Do not patch. Investigate, then `reset` and rebuild if core was altered |
| `graph.*` fails | Neo4j differs from PostgreSQL | Rerun `graph-sync` |
| Connection stalls on `localhost` | Windows tries IPv6 first; containers bind IPv4 only | Keep `127.0.0.1` in `.env`, as in `.env.example` |
| `changed after it was applied` from `migrate` | A migration file was edited after being applied | Add a new migration. During development: `p2p reset --yes` |

Every step records itself in `ops.pipeline_run`, with counts or the error. No alerts are sent anywhere; failure is a non-zero exit and a recorded row.

## Tests

```powershell
uv run pytest                     # everything
uv run pytest -m "not integration"  # without Neo4j
uv run ruff check .
uv run ruff format --check .
```

Tests use their own `p2p_test` database, recreated each session, so they never touch `p2p`. Neo4j Community has one database, so the graph tests rebuild the shared graph; run `uv run p2p graph-sync` afterwards.

## Shut down

```powershell
docker compose -f infra/docker-compose.yml --env-file .env down      # keep data
docker compose -f infra/docker-compose.yml --env-file .env down -v   # also delete volumes
uv run p2p reset --yes             # alternatively, drop only the pipeline schemas
```

Everything regenerates from the seed. Credentials never belong in this file.
