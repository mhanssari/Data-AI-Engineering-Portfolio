"""Session fixtures: an isolated database and the full two-batch pipeline, run once.

Tests run against a separate p2p_test database, recreated on each session, so they never touch a working `p2p`
database. Neo4j Community has a single database, so test_graph.py does rebuild the shared graph; run `p2p graph-sync`
afterwards if it should reflect `p2p` again (with the default seed the content is the same).
"""

import csv
import os
from dataclasses import dataclass
from pathlib import Path

os.environ["POSTGRES_DB"] = "p2p_test"

import psycopg
import pytest

from p2p_recon import conform, documents, dq, generate, graph, ingest, mdm, migrate, recon
from p2p_recon.config import get_settings
from p2p_recon.db import connect


def _database_reachable() -> bool:
    try:
        psycopg.connect(get_settings().conninfo("postgres"), connect_timeout=5).close()
        return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(scope="session")
def database():
    if not _database_reachable():
        pytest.fail(
            "PostgreSQL is not reachable. Start it: docker compose -f infra/docker-compose.yml --env-file .env up -d"
        )
    with psycopg.connect(get_settings().conninfo("postgres"), autocommit=True) as admin:
        admin.execute("drop database if exists p2p_test with (force)")
        admin.execute("create database p2p_test")
    migrate.migrate()


@dataclass
class Pipeline:
    data_dir: Path
    run_batch_1: int
    run_batch_2: int
    run_rerun: int
    expected: list[dict]
    conform_rerun_counts: dict
    ingest_rerun_counts: dict
    raw_rows_before_rerun: int
    raw_rows_after_rerun: int
    dq_after_batch_1: list


def _raw_row_count() -> int:
    tables = [
        "sap_goods_receipt",
        "ora_receipts",
        "sap_invoice_header",
        "ora_invoices",
        "sap_pr",
        "ora_requisitions",
    ]
    with connect() as conn:
        return sum(conn.execute(f"select count(*) as n from raw.{t}").fetchone()["n"] for t in tables)


@pytest.fixture(scope="session")
def pipeline(database, tmp_path_factory) -> Pipeline:
    """Batch 1, then batch 2, then a full rerun of every step with nothing new delivered."""
    data_dir = tmp_path_factory.mktemp("generated")
    generate.generate(data_dir)
    cutoff = generate.CUTOFF

    def steps() -> int:
        conform.conform()
        mdm.resolve_suppliers()
        return recon.run_matching(cutoff)["recon_run_id"]

    ingest.ingest_batch(data_dir / "batch_001")
    run_1 = steps()
    dq_batch_1 = dq.run_dq()
    ingest.ingest_batch(data_dir / "batch_002")
    run_2 = steps()
    before = _raw_row_count()
    ingest_again = ingest.ingest_batch(data_dir / "batch_001")
    ingest.ingest_batch(data_dir / "batch_002")
    conform_again = conform.conform()
    mdm.resolve_suppliers()
    run_3 = recon.run_matching(cutoff)["recon_run_id"]
    with (data_dir / "expected_outcomes.csv").open(encoding="utf-8") as handle:
        expected = list(csv.DictReader(handle))
    return Pipeline(
        data_dir,
        run_1,
        run_2,
        run_3,
        expected,
        conform_again,
        ingest_again,
        before,
        _raw_row_count(),
        dq_batch_1,
    )


@pytest.fixture(scope="session")
def loaded_documents(pipeline) -> dict:
    return documents.load_documents(pipeline.data_dir / "documents")


@pytest.fixture(scope="session")
def graph_synced(pipeline, loaded_documents) -> dict:
    return graph.sync_graph()


@pytest.fixture
def conn(database):
    with connect() as connection:
        yield connection
        connection.rollback()


@pytest.fixture
def dq_results(pipeline):
    return dq.run_dq()
