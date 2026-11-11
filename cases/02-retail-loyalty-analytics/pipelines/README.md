# Pipelines

The pipeline is the `loyalty` CLI ([src/loyalty/cli.py](../src/loyalty/cli.py)): `run-all` generates, then loads, checks freshness and builds each batch in order, then exports. The opt-in Airflow DAG that runs the same steps per delivery is in [orchestration](../orchestration/README.md).
