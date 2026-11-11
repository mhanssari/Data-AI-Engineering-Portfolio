# Airflow with the case installed in its own environment. dbt and Airflow pin different versions of shared
# libraries, so the case does not go into Airflow's environment: tasks call /opt/loyalty-venv/bin/loyalty.
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

FROM apache/airflow:3.4.0-python3.12

COPY --from=uv /uv /usr/local/bin/

# Airflow's own environment is a venv in /home/airflow/.local built on /usr/bin/python3.12; the case's
# environment uses the same base interpreter but stays separate.
ENV UV_PROJECT_ENVIRONMENT=/opt/loyalty-venv \
    UV_PYTHON=/usr/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

USER root
RUN mkdir -p /opt/loyalty-venv /opt/loyalty-project \
    && chown airflow:root /opt/loyalty-venv /opt/loyalty-project
USER airflow

WORKDIR /opt/loyalty-project
COPY --chown=airflow:root pyproject.toml uv.lock .python-version README.md ./
RUN --mount=type=cache,target=/home/airflow/.cache/uv,uid=50000 uv sync --frozen --no-dev --no-install-project
COPY --chown=airflow:root . .
RUN --mount=type=cache,target=/home/airflow/.cache/uv,uid=50000 uv sync --frozen --no-dev

WORKDIR /opt/airflow
