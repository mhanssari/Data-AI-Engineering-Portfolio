# Linux image for the case's CLI and tests, from uv.lock (same pattern as case 02, decision 0005 there).
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

FROM python:3.12.16-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

FROM python:3.12.16-slim-trixie
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
ARG UID=1000
RUN groupadd --gid "$UID" app \
    && useradd --uid "$UID" --gid app --create-home app \
    && mkdir -p /app && chown -R app:app /app
COPY --from=build --chown=app:app /opt/venv /opt/venv
COPY --from=build --chown=app:app /app /app
USER app
WORKDIR /app

ENTRYPOINT ["payrecon"]
CMD ["--help"]
