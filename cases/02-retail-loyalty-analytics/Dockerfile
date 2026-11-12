# Linux image for the whole case: Python 3.12, dbt Core, DuckDB and the loyalty CLI, from uv.lock.
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

# --- build: resolve the environment with uv; its download cache never reaches the final image
FROM python:3.12.16-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/
# The environment lives outside /app, so mounting the project folder over /app for development does not hide it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: editing code does not invalidate this layer.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

# --- runtime
FROM python:3.12.16-slim-trixie
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
ARG UID=1000
RUN groupadd --gid "$UID" app \
    && useradd --uid "$UID" --gid app --create-home app \
    && mkdir -p /app/warehouse /app/data/generated \
    && chown -R app:app /app
COPY --from=build --chown=app:app /opt/venv /opt/venv
COPY --from=build --chown=app:app /app /app
USER app
WORKDIR /app

ENTRYPOINT ["loyalty"]
CMD ["--help"]
