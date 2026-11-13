"""Durable unwatermarked intake. Each raw batch, quarantine and commit record share one transaction."""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime

from psycopg import sql
from psycopg.types.json import Jsonb

from payrecon import db
from payrecon.config import CASE_ROOT
from payrecon.streaming.contract import ContractError, Dataset, decode

MIGRATIONS = CASE_ROOT / "sql/stream/migrations"


def require_stream_database(con) -> None:
    name = con.execute("select current_database()").fetchone()[0]
    if not name.startswith(("payments_stream", "payrecon_test_")):
        raise ValueError("stream intake requires a payments_stream* database, separate from batch")


def prepare() -> list[str]:
    name = os.environ.get("PGDATABASE", "")
    if not name.startswith("payments_stream"):
        raise ValueError("set PGDATABASE to a separate payments_stream* database")
    with db.connect("postgres") as admin:
        if not admin.execute("select 1 from pg_database where datname = %s", [name]).fetchone():
            admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    with db.connect() as con:
        require_stream_database(con)
        return db.migrate(con, MIGRATIONS)


def bind_dataset(con, dataset: Dataset) -> None:
    con.execute(
        "insert into stream.dataset(dataset_id,source_start,source_end) values (%s,%s,%s) "
        "on conflict(singleton) do nothing",
        [dataset.dataset_id, dataset.source_start, dataset.source_end],
    )
    actual = con.execute("select dataset_id,source_start,source_end from stream.dataset").fetchone()
    if actual != (dataset.dataset_id, dataset.source_start, dataset.source_end):
        raise ValueError("database is already bound to a different dataset or source clock")


@dataclass(frozen=True)
class Identity:
    cluster_id: str
    topic_id: str
    topic: str


@dataclass(frozen=True)
class Delivery:
    partition: int
    offset: int
    key: bytes | None
    value: bytes | None
    broker_time: datetime | None


class IntakeConflict(RuntimeError):
    pass


def persist_batch(
    con, dataset: Dataset, identity: Identity, generation: str, batch_id: int, records: list[Delivery]
) -> tuple[dict, bool]:
    require_stream_database(con)
    records = sorted(records, key=lambda row: (row.partition, row.offset))
    by_coordinate = {}
    for row in records:
        coordinate = row.partition, row.offset
        if coordinate in by_coordinate and by_coordinate[coordinate] != row:
            raise IntakeConflict("different content at the same Kafka transport coordinate")
        by_coordinate[coordinate] = row
    serialised = [
        [
            r.partition,
            r.offset,
            r.key.hex() if r.key is not None else None,
            r.value.hex() if r.value is not None else None,
            r.broker_time.isoformat() if r.broker_time else None,
        ]
        for r in records
    ]
    digest = hashlib.sha256(json.dumps(serialised, separators=(",", ":")).encode()).hexdigest()
    with con.transaction():
        con.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s,0))", ["payrecon/intake/" + dataset.dataset_id]
        )
        bind_dataset(con, dataset)
        registered = con.execute(
            "select dataset_id,cluster_id,topic_id,topic from stream.lineage "
            "where generation=%s and query_role='intake'",
            [generation],
        ).fetchone()
        if registered != (dataset.dataset_id, identity.cluster_id, identity.topic_id, identity.topic):
            raise ValueError("batch does not match its registered lineage")
        earlier = con.execute(
            "select input_hash,summary from ops.stream_batch "
            "where generation=%s and query_role='intake' and batch_id=%s",
            [generation, batch_id],
        ).fetchone()
        if earlier:
            if earlier[0] != digest:
                raise IntakeConflict("a committed batch ID was replayed with different input")
            return earlier[1], True

        con.execute(
            "create temporary table intake_stage (like stream.delivery including defaults) on commit drop"
        )
        with (
            con.cursor() as cursor,
            cursor.copy(
                'copy intake_stage(cluster_id,topic_id,partition,"offset",topic,key,value,broker_time) from stdin'
            ) as copy,
        ):
            for row in records:
                copy.write_row(
                    (
                        identity.cluster_id,
                        identity.topic_id,
                        row.partition,
                        row.offset,
                        identity.topic,
                        row.key,
                        row.value,
                        row.broker_time,
                    )
                )
        conflict = con.execute(
            "select 1 from intake_stage s join stream.delivery d "
            'using(cluster_id,topic_id,partition,"offset") where '
            "s.key is distinct from d.key or s.value is distinct from d.value "
            "or s.broker_time is distinct from d.broker_time limit 1"
        ).fetchone()
        if conflict:
            raise IntakeConflict("stored delivery changed at an existing Kafka coordinate")
        inserted = set(
            con.execute(
                "insert into stream.delivery select * from intake_stage "
                'on conflict do nothing returning partition,"offset"'
            ).fetchall()
        )
        summary = dict(
            input=len(records),
            new_deliveries=len(inserted),
            new_events=0,
            duplicate_events=0,
            replayed_deliveries=0,
            quarantined=0,
            blocking=0,
        )

        def quarantine(row, reason, detail, blocking=False):
            con.execute(
                'insert into stream.quarantine(cluster_id,topic_id,partition,"offset",reason,detail,blocking) '
                "values (%s,%s,%s,%s,%s,%s,%s)",
                [identity.cluster_id, identity.topic_id, row.partition, row.offset, reason, detail, blocking],
            )
            summary["quarantined"] += 1
            summary["blocking"] += int(blocking)

        for row in records:
            coordinate = row.partition, row.offset
            if coordinate not in inserted:
                summary["replayed_deliveries"] += 1
                continue
            inserted.remove(coordinate)
            try:
                event, event_hash = decode(row.key, row.value, dataset)
            except ContractError as error:
                quarantine(row, error.reason, str(error))
                continue
            prior = con.execute(
                "select semantic_hash from stream.intake_event where dataset_id=%s and event_id=%s",
                [dataset.dataset_id, event["event_id"]],
            ).fetchone()
            if prior:
                if prior[0] != event_hash:
                    quarantine(
                        row, "event_id_conflict", "immutable event ID has different semantic content", True
                    )
                else:
                    summary["duplicate_events"] += 1
                continue
            occupied = con.execute(
                "select event_id from stream.intake_event where dataset_id=%s "
                "and payment_id=%s and sequence_no=%s",
                [dataset.dataset_id, event["payment_id"], event["sequence_no"]],
            ).fetchone()
            if occupied:
                quarantine(
                    row, "sequence_conflict", "a different event already owns this payment sequence", True
                )
                continue
            con.execute(
                "insert into stream.intake_event(dataset_id,event_id,payment_id,sequence_no,semantic_hash,event) "
                "values (%s,%s,%s,%s,%s,%s)",
                [
                    dataset.dataset_id,
                    event["event_id"],
                    event["payment_id"],
                    event["sequence_no"],
                    event_hash,
                    Jsonb(event),
                ],
            )
            summary["new_events"] += 1
        con.execute(
            "insert into ops.stream_batch(generation,query_role,batch_id,input_hash,summary) "
            "values (%s,'intake',%s,%s,%s)",
            [generation, batch_id, digest, Jsonb(summary)],
        )
    return summary, False


def halt_on_conflict(summary: dict) -> None:
    if summary["blocking"]:
        raise IntakeConflict(
            "blocking source identity conflict archived; resolve it before resuming this checkpoint"
        )
