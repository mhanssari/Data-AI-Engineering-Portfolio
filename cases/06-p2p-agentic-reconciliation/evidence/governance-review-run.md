# Governance review run record

**Date:** 3 November 2026. Windows 11, PowerShell, Docker Desktop, Python 3.12, PostgreSQL 18 with pgvector, Neo4j 2026.09 Community, Streamlit 1.66.1, LangGraph 1.2.19. Default seed, synthetic data, offline provider. See [decision 0010](../docs/decisions/0010-admin-review-dashboard.md).

## Commands, as run

```powershell
uv run p2p reset --yes
uv run p2p run-all                       # exit 0, 53 s, includes validate-ontology
uv run p2p eval-retrieval                # exit 0
uv run p2p agent-eval --all-lines        # exit 0
uv run p2p agent-run --as ap_analyst_uk  # exit 0
uv run p2p agent-run --as ap_analyst_de  # exit 0
uv run p2p contracts-check               # exit 0, no problems
uv run p2p access-probe                  # exit 0, 83 attempts, 0 failed
uv run streamlit run src/p2p_recon/admin_app.py --server.address 127.0.0.1 --server.port 8601
uv run ruff check src tests              # clean
uv run ruff format --check src tests     # 68 files formatted
uv run pytest                            # 550 passed in 228 s
```

Two demonstration decisions were then recorded on the UK queue, one ACCEPT and one OVERRIDE with a reason, plus one admin review note asking for a follow-up. This gives the agreement rate and the notes something to show. The screenshots in [screenshots/](screenshots/) were taken in headless Edge against that state.

## Measured on the admin pages

| Figure | Result |
| --- | --- |
| Overview alerts from `alerts.yaml` | None raised |
| Data-quality checks; graph against PostgreSQL | 36 pass, 0 warn, 0 fail; no mismatches |
| Sampled real chains conforming to SHACL | 25 of 25, plus both examples behaving as designed |
| Planted lines passing every check in the agent evaluation | 42 of 42 |
| Hostile provider: AUTO_APPROVE drafts; stored | 13; 0 |
| Provider outage: held lines sent to a person | 22 of 22 |
| Another company's lines requested; denied | 42; 42, with no proposal |
| Live runs: proposals; citation validity; guard interventions | 22; 117 of 117; 0 |
| Agreement with people, on the two demonstration decisions | 50% (1 accept, 1 override). This shows the figure moves; it measures nothing |
| Access probe | 83 of 83 attempts as expected |
| Ontology classes without a table or graph label | 6: `BusinessDocument`, `CreditNote`, `DocumentLine`, `JournalEntry`, `Party`, `PaymentProposal`. Abstract or not yet projected, as the ontology README says |
| Reason codes written by recon but missing from the vocabulary | 0 |
| Reason concepts without a definition | 7 |

## Tests and mutation checks

| Slice | New tests | Rules broken on purpose and caught |
| --- | --- | --- |
| Role, notes, view log | 66 security tests | 18 of 18 |
| Overview and Agents | 30 | 18 of 18, after four tests were tightened |
| Ontology, Entities, Rules and states | 45 | 21 of 21, after one test was strengthened |
| Evaluations, Lineage, Access, audit packs | 19 | 18 of 18, after two tests were added |

Each mutant replaced one string in the source, ran the relevant test file and was then reverted. A result counted as caught only when a named test failed, so a broken baseline could not pass for a catch.

## Problems found and fixed

- **SHACL rejected every real reason code.** The vocabulary was passed to pySHACL only as the ontology graph, and a SPARQL constraint queries the data graph alone. The examples hid it, because the valid example has no exception.
  - **Fix:** `validate_graph` now adds the vocabulary to the data.
  - **Test:** a new test on real chains with exceptions failed before the fix and passes after.
- **The workflow drawing's notes overflowed their boxes in the browser.** Notes now put one flag per line.

## Not validated

- **Screenshots** show one window-height of each page, because Streamlit scrolls inside its own container.
- **No live model.** Every agent figure comes from the offline rule table.
- **The view log** records what the app showed, not every read by the role.
- **CI** has not run these tests on GitHub.
