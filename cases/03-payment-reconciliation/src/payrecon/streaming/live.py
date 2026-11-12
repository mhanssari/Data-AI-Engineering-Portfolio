"""Transactional admitted-event ledger and deterministic SQL projections. No Spark dependency here."""

import hashlib
import json
from collections.abc import Callable
from datetime import date, timedelta

from psycopg.types.json import Jsonb

from payrecon import generator
from payrecon.config import CASE_ROOT, FIRST_ARRIVAL, LAST_ARRIVAL, SEED
from payrecon.sql_files import read_sql
from payrecon.streaming.contract import Dataset, decode
from payrecon.streaming.intake import (
    Delivery,
    Identity,
    IntakeConflict,
    bind_dataset,
    require_stream_database,
)

LOCK = "payrecon/live-writer"
PROJECTION = CASE_ROOT / "sql/stream/project_live.sql"


def lock_writer(con, *, session: bool = False) -> None:
    function = "pg_try_advisory_lock" if session else "pg_try_advisory_xact_lock"
    if not con.execute(f"select {function}(hashtextextended(%s,0))", [LOCK]).fetchone()[0]:
        raise RuntimeError("another live writer or reference refresh owns this database")


def project(con, generation: str, payments: set[str]) -> None:
    if not payments:
        return
    con.execute("select set_config('TimeZone','UTC',true)")
    con.execute("select set_config('payrecon.live_generation',%s,true)", [generation])
    con.execute("create temporary table affected(payment_id text primary key) on commit drop")
    with con.cursor().copy("copy affected from stdin") as copy:
        for payment in sorted(payments):
            copy.write_row((payment,))
    con.execute(read_sql(PROJECTION))


def load_reference(con, arrival: date, *, seed: int = SEED) -> dict:
    """Independent seeded snapshot, using the same reference delivery calendar as batch."""
    require_stream_database(con)
    if not FIRST_ARRIVAL <= arrival <= LAST_ARRIVAL:
        raise ValueError("reference arrival is outside the seeded delivery calendar")
    world = generator.build_world(seed)
    merchants = sorted(
        (m.merchant_id, m.currency)
        for m in [*world.merchants, *world.late_merchants]
        if m in world.merchants or m.updated_at.date() + timedelta(days=1) <= arrival
    )
    digest = hashlib.sha256(json.dumps(merchants).encode()).hexdigest()
    with con.transaction():
        lock_writer(con)
        prior = con.execute(
            "select seed,as_of_arrival,content_hash from stream.reference_snapshot"
        ).fetchone()
        if prior:
            if prior[0] != seed or arrival < prior[1]:
                raise ValueError("reference seed cannot change and frontier cannot move backwards")
            if prior == (seed, arrival, digest):
                return dict(merchants=len(merchants), reused=True)
        con.execute("delete from stream.reference_merchant")
        with con.cursor().copy("copy stream.reference_merchant from stdin") as copy:
            for merchant in merchants:
                copy.write_row(merchant)
        con.execute(
            "insert into stream.reference_snapshot(seed,as_of_arrival,content_hash) values (%s,%s,%s) "
            "on conflict(singleton) do update set as_of_arrival=excluded.as_of_arrival,"
            "content_hash=excluded.content_hash,loaded_at=now()",
            [seed, arrival, digest],
        )
        generations = con.execute("select generation from stream.lineage where query_role='live'").fetchall()
        for (generation,) in generations:
            payments = {
                p
                for (p,) in con.execute(
                    "select distinct payment_id from stream.live_event where generation=%s", [generation]
                )
            }
            # Each projection uses ON COMMIT DROP temporary names; clear them between generations.
            with con.transaction():
                project(con, str(generation), payments)
            for table in (
                "affected_bucket",
                "walk",
                "walk_transition",
                "walk_event",
                "bad_identity",
                "projection_event",
                "affected",
            ):
                con.execute(f"drop table if exists pg_temp.{table}")
    return dict(merchants=len(merchants), reused=False)


def persist_live(
    con,
    dataset: Dataset,
    source: Identity,
    generation: str,
    batch_id: int,
    records: list[Delivery],
    *,
    before_commit: Callable[[], None] | None = None,
) -> tuple[dict, bool]:
    require_stream_database(con)
    # Hash the semantic admitted set. Deduplication may choose a different physical copy on a retry.
    events = sorted(
        (decode(r.key, r.value, dataset) for r in records), key=lambda pair: (pair[0]["event_id"], pair[1])
    )
    digest = hashlib.sha256(json.dumps([(e["event_id"], h) for e, h in events]).encode()).hexdigest()
    with con.transaction():
        lock_writer(con)
        bind_dataset(con, dataset)
        row = con.execute(
            "select dataset_id,cluster_id,topic_id,topic,query_role from stream.lineage where generation=%s",
            [generation],
        ).fetchone()
        if row != (dataset.dataset_id, source.cluster_id, source.topic_id, source.topic, "live"):
            raise ValueError("live batch does not match its registered lineage")
        prior = con.execute(
            "select input_hash,summary from ops.stream_batch where generation=%s and query_role='live' and batch_id=%s",
            [generation, batch_id],
        ).fetchone()
        if prior:
            if prior[0] != digest:
                raise IntakeConflict("live batch ID was replayed with a different admitted set")
            return prior[1], True
        if not con.execute("select 1 from stream.reference_snapshot").fetchone():
            raise ValueError("load the independent stream reference snapshot before live processing")
        summary = dict(input=len(events), new_events=0, duplicate_events=0, blocking=0)
        affected = set()
        for event, event_hash in events:
            stored = con.execute(
                "select event_id,semantic_hash from stream.live_event where generation=%s "
                "and (event_id=%s or (payment_id=%s and sequence_no=%s))",
                [generation, event["event_id"], event["payment_id"], event["sequence_no"]],
            ).fetchall()
            canonical = con.execute(
                "select event_id,semantic_hash from stream.intake_event where dataset_id=%s "
                "and (event_id=%s or (payment_id=%s and sequence_no=%s))",
                [dataset.dataset_id, event["event_id"], event["payment_id"], event["sequence_no"]],
            ).fetchall()
            if any(r != (event["event_id"], event_hash) for r in [*stored, *canonical]):
                con.execute(
                    "insert into stream.live_conflict values (%s,%s,%s,%s,%s,%s) on conflict do nothing",
                    [
                        generation,
                        batch_id,
                        event["event_id"],
                        event_hash,
                        Jsonb(event),
                        "immutable event or sequence conflict",
                    ],
                )
                summary["blocking"] += 1
            elif stored:
                summary["duplicate_events"] += 1
            else:
                con.execute(
                    "insert into stream.live_event(generation,event_id,payment_id,sequence_no,semantic_hash,event) "
                    "values (%s,%s,%s,%s,%s,%s)",
                    [
                        generation,
                        event["event_id"],
                        event["payment_id"],
                        event["sequence_no"],
                        event_hash,
                        Jsonb(event),
                    ],
                )
                summary["new_events"] += 1
                affected.add(event["payment_id"])
        project(con, generation, affected)
        con.execute(
            "insert into ops.stream_batch values (%s,'live',%s,%s,%s,now())",
            [generation, batch_id, digest, Jsonb(summary)],
        )
        if before_commit is not None:
            before_commit()
    return summary, False
