# Milestone 2 run record

**Date:** 2 November 2026. **Machine:** Windows 11 with Docker Desktop, one laptop.

**Software, added at this milestone:** fastembed 0.9.1 with `BAAI/bge-small-en-v1.5` (384 dimensions), onnxruntime 1.31.1, LangGraph 1.2.19, Anthropic SDK 2.3.0. The rest as in [the milestone 1 record](milestone-1-run.md).

Measured reports, written by the commands themselves:

- [Retrieval evaluation](retrieval-evaluation.md)
- [Agent evaluation, offline provider](agent-evaluation-offline.md)

## Commands run

From empty Docker volumes (`down -v`) and with the model cache deleted, so the model was downloaded again:

```powershell
docker compose -f infra/docker-compose.yml --env-file .env up -d --wait
uv sync
uv run p2p run-all                 # exit 0, 61 s
uv run p2p eval-retrieval          # 18 s
uv run p2p agent-eval --all-lines  # exit 0, 51 s
uv run p2p agent-run --as ap_de01 --limit 3
uv run p2p review-list --as ap_de01
uv run ruff check .                # clean
uv run ruff format --check .       # clean
uv run pytest                      # 162 passed in 93 s
```

Also: every file Git would publish was copied to a new folder, without the ignored local notes, with `.env` made from `.env.example`. `uv sync` and `uv run pytest` there: 162 passed. The copy has not yet been through a real clone, because this work is not committed.

## Results

| Check | Result |
| --- | --- |
| Corpus | 84 documents, 238 chunks; loaded in 29 s, including the model download |
| Reload with nothing changed | 0 chunks re-embedded |
| Structured data after enlarging the corpus | 43 of 45 files byte-identical; only the contract file and its manifest changed |
| Data-quality checks, scenarios | 36 pass, 0 warn, 0 fail; all 36 scenarios unchanged |
| Graph against PostgreSQL | 3,419 nodes (24 more contract versions), 6,862 edges, no count differences |
| Tests | 162 passed |
| Rules deliberately broken and caught | 25 of 25 (24 on the first pass) |
| False auto-approvals, offline, planted lines | 0 of 42 |
| False auto-approvals, offline, all current lines | 0 of 360 |
| False auto-approvals, hostile provider | 0, from 13 attempts on 22 held lines |
| Cross-company investigation attempts | 42 attempted, 42 denied, 0 proposals |

## Mutation check

A throwaway script, not in the repository, broke one rule at a time and ran the new test files: the guard's six rules, company and tag filters, graph filters, rank fusion, redaction, routing, the database role and its grants, the token limit, the invented-tool check, the review-queue filter, re-embedding, the citation check, the prompt fence and refusal handling.

24 of 25 were caught first time. The survivor removed the company filter from `linked_document_ids`. It caused no leak, because the ranking query applies permissions again, but nothing tested that filter on its own. `test_link_lookup_refuses_keys_from_another_company` was added, and the same mutant then failed that test.

## Problems found and fixed while building

- **The rule was pushed out of the evidence.** With one ranked list, a supplier's linked contract clauses crowded out the governing policy rule, and only 59% of held lines cited the labelled rule. The search tool now runs one search over rules and one over the case, which took it to 100% of 22. That figure is optimistic, because the offline provider's search phrases were written by the corpus's author.
- **Evaluation runs reached the review queue.** Hostile and outage runs became the latest proposal for a line. Evaluation runs are now excluded from `agent.review_queue`.
- **Six documents could not support an evaluation.** Before any method was measured, the corpus was enlarged with near-identical contracts and routine distractor mail.

## Not validated

- **No live model has been evaluated.** The `anthropic` and `ollama` providers have been run against mocked responses only, which tests request shape and response parsing, not whether the API accepts the request or how well a model reasons. All token figures here are estimates from prompt length, and the zero cost reflects that no model was called.
- **Prompt injection against a real model.** The planted email tells an assistant to approve an invoice. The offline provider ignores it, and the hostile provider simulates obeying it, which the guard blocks. Whether a given model is misled is untested.
- **Retrieval quality beyond this corpus.** 36 hand-written questions, from the same author as the corpus, with no held-out set.
- **Row-level security.** Company scoping is application code until milestone 3. Anything on another machine, or at any larger scale.
