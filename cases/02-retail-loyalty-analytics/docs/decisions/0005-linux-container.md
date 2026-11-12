# 0005 Run in a Linux container; keep a local path without Docker

Status: accepted

Date: 11 November 2026

## Context

Development happens on Windows, while CI runs on Linux and Airflow will run in Linux containers. Differences in paths, line endings and file locking between the two should show up on the developer's machine, not first in CI. The portfolio rule that a reviewer can run a case without Docker still applies.

## Options

1. **Local Python only (uv).** No Docker needed. Windows-only behaviour can hide until CI.
2. **Docker only.** One environment everywhere. Docker becomes a prerequisite for every reviewer.
3. **A Linux image and Compose service as the main way to run, with the uv path kept working.** Both use the same `uv.lock`.

## Decision and rationale

Option 3. `Dockerfile` builds the environment from `uv.lock` in a build stage and copies only the environment and code into a `python:3.12.16-slim-trixie` runtime, as a non-root user. `compose.yaml` mounts the case folder, so code edited on the host runs in Linux without a rebuild. The DuckDB file lives on a named volume, because DuckDB relies on file locks that a Windows folder mounted into a container does not support reliably, and the volume is faster.

## Consequences and validation

- Verified on 11 November 2026: `docker compose run --rm loyalty run-all` exit 0 (46 s); `pytest` 25 passed and `ruff` clean inside the container; the generator writes byte-identical extracts on Windows and in the container.
- Image about 730 MB, of which 420 MB is the Python environment (dbt Core's dependencies and DuckDB). uv's download cache stays out of the image.
- The warehouse inside the volume is not visible from Windows. Outputs meant for the host, such as the Parquet exports for Power BI, will be written to the mounted folder.
- On a Linux host whose user id is not 1000, build with `--build-arg UID=$(id -u)` so files written to the mounted folder belong to that user.
