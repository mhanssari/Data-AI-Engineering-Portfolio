# One Airflow for the portfolio. Each case is installed in its own environment under /opt/cases/<case>, from the
# case's own uv.lock, because cases pin libraries (dbt, PySpark) that conflict with Airflow's and with each other.
# DAGs and source code are mounted at run time (compose.yaml); the image holds only the environments.
# Build from the repository root: docker build -f platform/airflow/Dockerfile .
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

FROM apache/airflow:3.4.0-python3.12

COPY --from=uv /uv /usr/local/bin/

# Airflow's own environment is a venv in /home/airflow/.local on /usr/bin/python3.12; case environments share the
# interpreter, never the packages.
ENV UV_PYTHON=/usr/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

USER root
RUN mkdir -p /opt/cases /opt/portfolio && chown -R airflow:root /opt/cases /opt/portfolio
USER airflow

# Case 02, retail loyalty analytics. The project sits where the repository is mounted at run time, so its editable
# install resolves to the mounted source; the environment sits outside the mount.
ARG CASE02=/opt/portfolio/cases/02-retail-loyalty-analytics
COPY --chown=airflow:root cases/02-retail-loyalty-analytics/pyproject.toml cases/02-retail-loyalty-analytics/uv.lock cases/02-retail-loyalty-analytics/.python-version cases/02-retail-loyalty-analytics/README.md ${CASE02}/
COPY --chown=airflow:root cases/02-retail-loyalty-analytics/src ${CASE02}/src
RUN --mount=type=cache,target=/home/airflow/.cache/uv,uid=50000 \
    cd ${CASE02} && UV_PROJECT_ENVIRONMENT=/opt/cases/retail-loyalty uv sync --frozen --no-dev

ARG CASE03=/opt/portfolio/cases/03-payment-reconciliation
COPY --chown=airflow:root cases/03-payment-reconciliation/pyproject.toml cases/03-payment-reconciliation/uv.lock cases/03-payment-reconciliation/.python-version cases/03-payment-reconciliation/README.md ${CASE03}/
COPY --chown=airflow:root cases/03-payment-reconciliation/src ${CASE03}/src
RUN --mount=type=cache,target=/home/airflow/.cache/uv,uid=50000 \
    cd ${CASE03} && UV_PROJECT_ENVIRONMENT=/opt/cases/payment-reconciliation uv sync --frozen --no-dev

WORKDIR /opt/airflow
