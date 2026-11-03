# Operational runbook

Verified on Windows 11 with Docker Desktop, in PowerShell, on 2 November 2026. Run everything from the case folder. The default path needs no API key. The first document load downloads the embedding model to `data/models` (64 MB, ignored by Git).

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
| `uv run p2p validate-ontology` | Validates the ontology examples and a fixed sample of real invoice chains against SHACL, recording each in `ops.dq_result`. `--sample` | New results row per run |
| `uv run p2p load-documents` | Chunks and embeds `data/generated/documents` into `docs` | Embeds only changed text; removes chunks of removed documents |
| `uv run p2p search "<question>" --as <user>` | Searches as a user. `--method keyword, vector, hybrid or hybrid_graph`; `--invoice SOURCE_SYSTEM:id` for the graph | Read-only |
| `uv run p2p eval-retrieval` | Recall@k and MRR per method; writes `evidence/retrieval-evaluation.md` | Overwrites the report |
| `uv run p2p agent-run --as <user>` | Investigates the user's held lines. `--provider`, `--limit` | New run; a later proposal supersedes an earlier one in the queue |
| `uv run p2p agent-eval` | Runs the planted scenarios plus hostile, outage and access runs; writes `evidence/agent-evaluation-<provider>.md`; exit 1 on any false approval. `--provider`, `--all-lines` | New runs, excluded from the review queue |
| `uv run p2p review-list --as <user>` | Open proposals for the user's companies | Read-only |
| `uv run streamlit run src/p2p_recon/review_app.py --server.address 127.0.0.1` | The review page: queue, explanation, citations, graph path, decision | Decisions append |
| `uv run p2p trace <id>` | From a proposal back to tool calls, citations, source files and datasets. `--out file.md`. Uses the pipeline's login | Read-only |
| `uv run p2p contracts-check` | Checks every contract against the database and its last release; exit 1 on a problem. `--as-of` sets the freshness clock | Read-only |
| `uv run p2p review-decide <id> ACCEPT --as <user>` | Records `ACCEPT`, `OVERRIDE` or `QUERY`, with an optional `--note`. Posts and pays nothing | Appends |
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

## Use a live model

Off by default. **Live calls cost money with Anthropic; neither provider has been run against a live model yet, so treat the first run as a test.**

- **Claude:** in `.env`, set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`. The default model is `claude-opus-5-5` at `medium` effort (`ANTHROPIC_MODEL`, `ANTHROPIC_EFFORT`). If a safety classifier declines a request, the API can re-run it on a fallback model.
- **Ollama:** install it, pull a model, and set `LLM_PROVIDER=ollama` and `OLLAMA_MODEL`.

Try one line first with `uv run p2p agent-run --as ap_analyst_uk --limit 1`. Then `uv run p2p agent-eval` writes a report per provider. That runs 22 lines through two to four model calls each. Tokens, latency and cost are logged in `agent.step`, and costs for models missing from `PRICES` in `agents/providers.py` show as unknown.

Follow one proposal back to its evidence:

```sql
select * from agent.proposal where proposal_id = 1;
select * from agent.citation where proposal_id = 1;
select s.agent_name, s.outcome, t.tool_name, t.arguments, t.result, t.status
from agent.step s left join agent.tool_call t using (step_id)
where s.agent_run_id = (select agent_run_id from agent.proposal where proposal_id = 1)
  and s.invoice_id = (select invoice_id from agent.proposal where proposal_id = 1)
order by s.step_id, t.tool_call_id;
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
| A proposal says `NEEDS_HUMAN_REVIEW` with `provider_failure` | The model was unreachable, refused or gave an unusable reply. The error is in `agent.step.error` | Fix the provider and run again; a new proposal supersedes this one |
| Agents find no graph context | Neo4j is empty or stale | Run `graph-sync`. After `pytest` the graph holds test data until you do |
| Connection stalls on `localhost` | Windows tries IPv6 first; containers bind IPv4 only | Keep `127.0.0.1` in `.env`, as in `.env.example` |
| `changed after it was applied` from `migrate` | A migration file was edited after being applied | Add a new migration. During development: `p2p reset --yes`. A database built before milestone 3 needs this once, because migration 005 changed |
| `Set APP_DB_PASSWORD` from `migrate` | An older `.env` | Add `APP_DB_PASSWORD` from `.env.example` |
| `password authentication failed for user "p2p_app"` | `migrate` has not run since the password was set or changed | Run `uv run p2p migrate` |
| `contracts-check` reports a problem | The database or a contract changed | Fix the drift, or update the contract with the right version bump, and copy it to `contracts/released/` when released |
| `permission denied` from a person's command or the page | Row-level security or a grant refused it, as designed | See [security](security.md) for what each role may do |

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
