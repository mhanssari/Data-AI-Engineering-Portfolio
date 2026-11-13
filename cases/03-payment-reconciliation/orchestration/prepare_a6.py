"""Prepare a separate Airflow demonstration database and its synthetic source deliveries.

Run once with the case Python inside Airflow. Refuse to overwrite an existing database. The ordinary case
database and the platform's metadata database are untouched. The DAG then loads arrivals 22 and 23 October.
"""

import os
from datetime import date
from pathlib import Path

from psycopg import sql

from payrecon import db, generator, loader
from payrecon.config import arrival_days

for setting in ("HOST", "PORT", "DATABASE", "USER", "PASSWORD"):
    os.environ[f"PG{setting}"] = os.environ[f"CASE03_PG{setting}"]
os.environ["PGCONNECT_TIMEOUT"] = "10"
database = os.environ["PGDATABASE"]
if not database.startswith("payments_airflow"):
    raise SystemExit("Use a separate payments_airflow* database for this demonstration")
with db.connect("postgres") as admin:
    if admin.execute("select 1 from pg_database where datname = %s", [database]).fetchone():
        raise SystemExit(f"{database} already exists; preparation refuses to replace it")
    admin.execute(sql.SQL("create database {}").format(sql.Identifier(database)))

data = Path("/opt/airflow/payment_reconciliation/data")
generator.generate(data)
with db.connect() as con:
    db.migrate(con)
    for day in arrival_days():
        if day >= date(2026, 10, 22):
            break
        loader.load_arrival(con, data / f"arrival_{day}")
    last = con.execute("select max(arrival_date), count(*) from ops.file").fetchone()
    print(f"Prepared {database}: last loaded arrival {last[0]}, {last[1]} files; 22 and 23 await the DAG")
