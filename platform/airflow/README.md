# Shared Airflow

One Airflow for the portfolio's cases: Airflow 3.4.0 standalone, with a PostgreSQL metadata database, on one machine. It is opt-in. Every case also runs through its own command line without it.

| File | Purpose |
| --- | --- |
| [Dockerfile](Dockerfile) | `apache/airflow:3.4.0-python3.12`, plus each case's Python environment under `/opt/cases/<case>`, built from the case's own `uv.lock` |
| [Dockerfile.dockerignore](Dockerfile.dockerignore) | The build context is the repository root; only the files the image installs are sent |
| [compose.yaml](compose.yaml) | Airflow and its metadata database. It mounts the repository and each case's DAG folder |

## How cases plug in

- **Environment.** Each case gets its own environment in the image. Cases pin libraries such as dbt and PySpark that conflict with Airflow's and with each other, so nothing is installed into Airflow's own environment.
- **DAGs.** A case's DAG folder is mounted at `/opt/airflow/dags/<case>`. The DAG calls the case's CLI from its environment, so all logic stays in the case.
- **Settings.** They travel with the DAG's tasks as environment variables. The platform holds no case configuration.
- **Reaching case services.** A case's own services (a database, a broker) publish a port on this machine; DAGs reach them at `host.docker.internal`. Each case keeps its own Compose file and runs without the platform.

| Case | DAG | Environment |
| --- | --- | --- |
| [02 Retail loyalty](../../cases/02-retail-loyalty-analytics/orchestration/README.md) | `retail_loyalty` | `/opt/cases/retail-loyalty` |

## Run

Needs Docker and about 4 GB of memory. From this folder:

```powershell
docker compose up -d --build        # http://127.0.0.1:8080, no login: every user is an admin, bound to this machine only
docker compose exec airflow airflow dags list
docker compose down                 # add -v to delete Airflow's state
```

Each case's README gives its own backfill and verification commands.

## Adding a case

1. Add a stage to the [Dockerfile](Dockerfile) that installs the case's environment from its `uv.lock`, and add its files to the [allow-list](Dockerfile.dockerignore).
2. Mount the case's DAG folder in [compose.yaml](compose.yaml).
3. Add the case's DAG check to the [platform workflow](../../.github/workflows/platform-airflow.yml).

## Not production

One container runs the scheduler, DAG processor, API server and triggerer, with a local executor. There are no workers, no high availability, no secrets backend, and no authentication beyond binding to 127.0.0.1.
