# Milestone 3 run record

**Date:** 2 November 2026. **Machine:** Windows 11 with Docker Desktop, one laptop.

**Software, added at this milestone:** Streamlit 1.66.1. The rest as in [the milestone 1](milestone-1-run.md) and [milestone 2](milestone-2-run.md) records. Screenshots used Playwright with the installed Edge, in a temporary environment; it is not a project dependency.

## Commands run

On Windows, from empty Docker volumes. The embedding model was already cached here; a fresh download was verified at milestone 2.

```powershell
docker compose -f infra/docker-compose.yml --env-file .env up -d --wait
uv sync --frozen
uv run p2p run-all                  # exit 0, 47 s
uv run p2p dq                       # 36 pass, 0 warn, 0 fail
uv run p2p eval-retrieval           # exit 0, 16 s
uv run p2p agent-eval --all-lines   # exit 0, 53 s
uv run p2p contracts-check          # exit 0
uv run p2p agent-run --as ap_analyst_uk
uv run p2p agent-run --as ap_analyst_de
uv run p2p trace 452 --out evidence/trace-S14.md
uv run ruff check .                 # clean
uv run ruff format --check .        # clean
uv run pytest                       # 368 passed in 108 s
```

In a Linux container (`python:3.12-bookworm`, uv 0.13.6), with the same steps as the workflow against the PostgreSQL and Neo4j containers: `uv sync --frozen`, ruff, the SHACL tests (14 passed), `run-all`, `contracts-check`, `agent-eval` and `pytest` (368 passed in 97 s). All exited 0. The workflow file itself passed `actionlint`. Its actions are pinned to commit hashes, confirmed against each tag.

## Results

| Check | Result |
| --- | --- |
| Tests | 368 passed on Windows and on Linux |
| Contracts | 4 of 4 clean against the database and their last release |
| Rows each analyst sees | Their own company only, on every company table, in every parent-linked table and through every view |
| Bank numbers | Refused for all five roles; masked view shows the last four characters |
| Decisions | Analyst allowed on an ordinary exception; refused on a failed control, another company's line and another person's name. Controller allowed. Auditor and agent refused |
| Trail | Update, delete and truncate refused on proposals, citations, decisions and the audit log, including for the owner |
| Trace | [Proposal 452, scenario S14](trace-S14.md): 8 steps, 2 cited rows, 3 cited documents, 9 core rows traced to file and row, and dataset lineage |
| Retrieval with row-level security on | Recall and MRR unchanged. Hybrid p50 35 ms, against 35 ms before |
| Agent evaluation with row-level security on | Unchanged: 0 false auto-approvals; 0.24 s median per invoice |

## Screenshots

From the running page, on real data in the `p2p` database:

- [An analyst's view of S14](screenshots/review-page.png), with the queue, explanation, SQL figures, a citation and the graph path
- [The same analyst after recording a decision](screenshots/review-decision-recorded.png)
- [A failed control, which the analyst cannot clear](screenshots/review-control-needs-controller.png)
- [The auditor's read-only view](screenshots/review-auditor-read-only.png)
- [The German analyst's queue](screenshots/review-queue-de01.png), which holds DE01 lines only

The explanations are the offline provider's fixed wording, so they read mechanically. They show where a model's explanation would go, not what one would say.

## Mutation check

A throwaway script, not in the repository, broke one rule at a time and ran the new test files: 31 of 31 were caught. They covered row-level security on individual tables, open parent policies, decision rights, the audit triggers, the application login's privileges, view ownership, masking, grants, on-behalf-of, the contract checks, the trace and the page.

Two needed a second attempt, because the first attempt had not actually tested them. Two mutants did not apply because reformatting had changed the text they looked for, and one retry reported "caught" only because it left a syntax error. Both were re-run properly, and each failed on the intended test.

## Problems found and fixed while building

- **A contract check crashed instead of reporting.** With a contracted column gone, the quality check raised a database error instead of a finding. It now reports the missing column and skips that check.
- **The auditor could record decisions.** `review-decide` allowed it before this milestone. It is now refused by the database.
- **`TRUNCATE` could empty the trail,** because row triggers do not fire on it. Statement triggers now refuse it.
- **Agent steps are updated when they finish,** so they are not append-only. A trigger-fed audit log holds the record that is.

## Not validated

- **CI on GitHub.** The workflow has not run there. The Linux rehearsal is close, but it is not the same as GitHub's service containers and runners. Pushing a workflow file also needs a Git login with the `workflow` scope, which has not been confirmed.
- **Any live model,** real token counts or cost, or prompt injection against a real model.
- **Real sign-in.** Roles are assumed from one login, and the page's selector simulates who is using it.
- **Freshness in operation.** The 48-hour SLA runs against an injected clock, because the synthetic data has fixed dates. It is a proposal, not a measured service level.
- **Retention.** Periods are proposed, and nothing deletes anything.
- **The review page under real use.** Tested with Streamlit's `AppTest` and driven once in a browser. There has been no usability testing with a finance user, and no concurrency testing.
- **A real clone.** Not committed at the time of this run; the milestone 2 clean-copy check has not been repeated.
