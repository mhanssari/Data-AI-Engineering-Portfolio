# Case 03 CI: local workflow validation

Recorded 12 November 2026 on Windows with Docker Desktop. The [Case 03 workflow](../../../.github/workflows/case-03-ci.yml) was validated locally in its Linux image against a disposable PostgreSQL 18.6 service, then committed and pushed as `2f3522d`. Its first [GitHub run](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43238741131) passed; remote results are recorded below.

## Checks and results

| Check | Result |
| --- | --- |
| Locked image build | Passed: `docker build -t payment-reconciliation:ci .` |
| CLI smoke check | `docker run --rm payment-reconciliation:ci --help`: exit 0 |
| Ruff | `ruff check .` and `ruff format --check .` inside the image: passed |
| Full suite, database required | **47 passed in 250.27 seconds; 0 skipped, 0 failures, 0 errors** |
| JUnit report | Written to the mounted `evidence/ci/pytest.xml`; 47 tests, zero errors/failures/skips |
| PostgreSQL unavailable | Selected loader test with `PAYRECON_REQUIRE_DB=1`, loopback port 1: **exit 1, 1 setup error in 0.06 seconds**, rather than skipping |
| Workflow syntax | `actionlint .github/workflows/case-03-ci.yml`: passed |
| Documentation links / whitespace | Relative targets exist; `git diff --check` passed |

The two new tests inject a refused connection and verify that the fixture fails when PostgreSQL is required, while retaining optional local skipping without that setting. The other 45 tests exercise the seeded generator, landing, core state and reconciliation behaviour already documented in [test guidance](../tests/README.md).

## Reproduction of the positive run

From the case directory, after building the image and creating `evidence/ci`:

```powershell
docker run -d --rm --name case03-ci-postgres -p 127.0.0.1:55434:5432 `
    -e POSTGRES_DB=payments_ci -e POSTGRES_USER=payments_ci -e POSTGRES_PASSWORD=case03-ci-only `
    --health-cmd 'pg_isready -U payments_ci -d payments_ci' `
    --health-interval 5s --health-timeout 3s --health-retries 20 postgres:18.6-alpine
docker inspect --format '{{.State.Health.Status}}' case03-ci-postgres
# Wait for healthy, then:
docker run --rm --add-host host.docker.internal:host-gateway `
    -e PGHOST=host.docker.internal -e PGPORT=55434 -e PGDATABASE=payments_ci `
    -e PGUSER=payments_ci -e PGPASSWORD=case03-ci-only -e PGCONNECT_TIMEOUT=5 `
    -e PAYRECON_REQUIRE_DB=1 -v "${PWD}/evidence/ci:/reports" `
    --entrypoint pytest payment-reconciliation:ci -q --junitxml=/reports/pytest.xml
docker stop case03-ci-postgres
```

These are disposable test credentials. The test service is separate from the running Case 03 PostgreSQL on port 55433. Tests create and drop their own databases, and stopping this `--rm` container removes its temporary service state. No existing case database or shared Airflow volume was reset.

The GitHub job uses its own PostgreSQL service and the same test settings. It passes the runner's UID into the existing Dockerfile so the non-root container user can write the report mount. Locally, the image used the default UID 1000 on Docker Desktop. The report directory is ignored by Git and excluded from image builds.

## First GitHub run

[Run 43238741131](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43238741131), commit `2f3522dfeb1c0858613499cd88c04126ef0413f7`, succeeded on 12 November 2026. The Ubuntu job started at 14:35:20 UTC and completed at 14:38:07 UTC. Service initialisation, locked image build, CLI smoke, lint/format, required-database tests and report upload all passed.

Downloaded the `case-03-pytest` artifact with `gh run download` and inspected its JUnit XML: **47 tests, 0 errors, 0 failures, 0 skipped; 134.390 seconds**. This verifies the actual runner networking, non-root report write and artifact upload as well as the test results. The local and GitHub durations come from different environments and are not a performance comparison. Pull-request triggering is configured but was not separately exercised by opening a PR.

## Previously pushed milestone

The Airflow integration was separately committed and pushed as `ba8d9c9` to `case-03-payments`. Its [Platform Airflow workflow](https://github.com/mhanssari/Data-AI-Engineering-Portfolio/actions/runs/43236947315) passed on GitHub: image build, both case DAG checks and both isolated CLI smoke checks. That result does not establish the new database workflow's remote status.
