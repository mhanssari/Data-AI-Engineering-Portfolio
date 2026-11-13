# Milestone 1: clean batch setup and evidence review

Recorded 12 November 2026. **The batch milestone is validated locally and its database CI passes on GitHub.** Streaming and failure/latency measurement remain future milestones; this does not mark the whole case complete.

## Environment and isolation

Cloned committed branch `case-03-payments` at `2f3522d` from the local repository into a fresh temporary checkout. The clone contained tracked files only: no existing `.env`, generated sources, warehouse or ignored planning notes. Docker image/dependency caches were retained; this was not a cold-start benchmark.

Used Docker Desktop on Windows, Python 3.12.16 and PostgreSQL 18.6. Created `.env` from `.env.example` with disposable credentials and port 55435. Compose project `case03-clean` owned a newly created `case03-clean_pgdata` volume and the `payments_clean` database. A temporary Compose override selected image tag `payment-reconciliation:clean-check`, preserving the existing case image. The original PostgreSQL on port 55433 and all shared Airflow state were untouched.

## Commands actually run

In the fresh case folder, using the project/override flags above for isolation:

```powershell
docker compose -p case03-clean -f compose.yaml -f compose.clean.yaml build payrecon
docker compose -p case03-clean -f compose.yaml -f compose.clean.yaml up -d --wait postgres
docker compose -p case03-clean -f compose.yaml -f compose.clean.yaml run --rm payrecon generate
docker compose -p case03-clean -f compose.yaml -f compose.clean.yaml run --rm payrecon migrate
docker compose -p case03-clean -f compose.yaml -f compose.clean.yaml run --rm payrecon load --arrival all
docker compose -p case03-clean -f compose.yaml -f compose.clean.yaml run --rm payrecon build-core
docker compose -p case03-clean -f compose.yaml -f compose.clean.yaml run --rm payrecon reconcile
docker compose -p case03-clean -f compose.yaml -f compose.clean.yaml run --rm payrecon run-all
```

All commands exited 0. The generator produced 31 arrival folders. All three migrations applied to the empty database. The explicit full load preceded the replay; `run-all` therefore exercised checksum-based skipping on already landed files and rebuilt each historical arrival from its as-of inputs. This audit does not claim a second empty-database landing replay.

The README now includes an explicit image-build command so a reviewer refreshes the locked environment for the selected checkout rather than relying on an older local tag.

## Measured fixture counts and outcomes

Counts were checked directly in PostgreSQL after replay:

| Item | Count |
| --- | ---: |
| Arrival folders / distinct dates with reconciliation results | 31 |
| Loaded distinct file name/checksum pairs | 126 |
| Raw merchant rows / final core merchants | 41 / 41 |
| Raw valid event rows | 177,325 |
| Distinct core events | 177,324 |
| Core payments | 60,367 |
| Settlement lines, raw and core | 49,365 each |
| Recorded illegal transitions | 1 |
| Matched payments on 1 November | 47,223 |
| Open exceptions on 1 November | 7, exactly the planted outcomes |
| Non-zero unexplained control-total groups across all 31 dates | 0 |

P4's malformed event was quarantined during loading; P1's duplicate delivery remains in raw and is deduplicated in core. The seven final exceptions comprise one missing settlement, one unexpected line, one amount mismatch, one fee mismatch, one duplicate settlement, and the two currency-specific sides of the planted currency mismatch. No currency amounts were combined to produce these counts.

`ops.run` contained 32 successful executions over 31 distinct arrival dates: the explicit final-date reconciliation plus the 31-date replay. No failed or unfinished execution remained. Every replay printed `checks passed`. The late USD file caused the expected missing/pending amounts on 22 October, then resolved on 23 October; full scheduled recovery and rerun evidence remains in the [Airflow record](airflow-late-file-run.md).

## CI and acceptance coverage

[GitHub run 43238741131](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43238741131), commit `2f3522d`, passed the locked image build, CLI smoke, lint/format and database suite. Its downloaded JUnit artifact confirms **47 tests, zero failures/errors/skips**. Report permissions and artifact upload passed on the actual Ubuntu runner. See [CI evidence](ci-local-run.md) for commands and durations.

The existing tests cover batch acceptance A1–A8: duplicate delivery and corrected re-issues; independent event-state replay and out-of-order sequences; illegal transitions and over-refunds; row quarantine and arrival rejection; per-currency control totals and deliberate check corruption; grace and late-file/reference recovery; unchanged historical reruns; and genuine double settlement versus an identical file resend. The clean audit repeated the batch commands, rather than rerunning the same full pytest suite after GitHub had passed it.

## Review findings and remaining limits

- Added missing grains for raw merchant landing and migration metadata; clarified file identity and the single-row core-build key against actual DDL.
- Clarified the proposed settlement match rate: numerator and denominator both exclude payments still in grace, and an empty denominator returns NULL. This corrects an ambiguous consumption definition; no implemented reconciliation calculation changed.
- Defined stored exception age using the SQL's processed-through date, and distinguished capture-date grouping from the last due date used for grace.
- Labelled consumption summaries that are not implemented separately, and recorded the latest-reference pricing and single-writer limits in the README.
- No transformation code changed in this review. No cloud deployment, full-month scheduled Airflow backfill, injected live sensor timeout/retry, streaming recovery or throughput/latency benchmark is claimed.

## Cleanup and next step

Removed only the isolated `case03-clean` containers, network and volume with its Compose `down -v`, then verified and removed the temporary checkout. The original case services and shared platform volumes were preserved.

Next: design milestone 2's streaming contract and shared Kafka configuration under `platform/kafka/`. Agree event-time lateness/retention and checkpoint/backfill boundaries before implementation; financial matching and money logic stay deterministic in SQL.
