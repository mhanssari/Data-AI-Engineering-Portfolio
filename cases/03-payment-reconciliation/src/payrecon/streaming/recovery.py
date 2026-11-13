"""Bounded, explicitly scoped late repair from a complete durable intake prefix. No Kafka/Spark imports."""

import hashlib
import json

from psycopg.types.json import Jsonb

from payrecon.streaming.contract import ContractError, Dataset, decode, identifier, stamp, utc
from payrecon.streaming.intake import require_stream_database
from payrecon.streaming.live import lock_writer, project
from payrecon.streaming.progress import _status

TABLES = ("stream.live_event", "stream.event_application", "stream.payment_latest", "stream.event_minute")


def checksum(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def plan(con, generation: str) -> dict:
    require_stream_database(con)
    with con.transaction():
        con.execute("set transaction isolation level repeatable read, read only")
        return _plan(con, generation)


def _plan(con, generation: str) -> dict:
    row = con.execute(
        "select l.dataset_id,d.source_start,d.source_end,l.cluster_id,l.topic_id,l.topic "
        "from stream.lineage l join stream.dataset d using(dataset_id) "
        "where generation=%s and query_role='live'",
        [generation],
    ).fetchone()
    if row is None:
        raise ValueError("repair requires a registered live generation")
    dataset_id, start, end, cluster, topic_id, topic = row
    intake = con.execute(
        "select generation from stream.lineage where dataset_id=%s and cluster_id=%s and topic_id=%s "
        "and topic=%s and query_role='intake' order by created_at desc limit 1",
        [dataset_id, cluster, topic_id, topic],
    ).fetchone()
    state = _status(con, generation)
    reference = con.execute(
        "select seed,as_of_arrival,content_hash from stream.reference_snapshot"
    ).fetchone()
    if not intake or not reference or not state["progress_covers_commits"]:
        raise ValueError(
            "repair requires a reference snapshot and reported progress through both sink commits"
        )
    if not state["intake_frontier"] or state["intake_frontier"] != state["live_frontier"]:
        raise ValueError("drain both readers to the same explicit frontier before repair")
    if state["blocking_conflicts"] or state["live_events_unverified_by_intake"]:
        raise ValueError("resolve immutable conflicts or unverified live events before repair")
    return dict(
        version=1,
        generation=str(generation),
        intake_generation=str(intake[0]),
        dataset_id=dataset_id,
        source_start=stamp(start),
        source_end=stamp(end),
        cluster_id=cluster,
        topic_id=topic_id,
        topic=topic,
        frontier=state["intake_frontier"],
        reference=dict(seed=reference[0], arrival=str(reference[1]), content_hash=reference[2]),
    )


def snapshot(con, generation: str) -> dict:
    return {
        table: dict(count=count, fingerprint=digest)
        for table in TABLES
        for count, digest in [
            con.execute(
                f"select count(*),md5(coalesce(string_agg(t::text,'|' order by t::text),'')) from {table} t where generation=%s",
                [generation],
            ).fetchone()
        ]
    }


def _source_events(con, spec: dict) -> dict:
    frontier = spec["frontier"]
    topic = spec["topic"]
    if set(frontier) != {topic} or not isinstance(frontier[topic], dict) or not frontier[topic]:
        raise ValueError("frontier must contain exactly the registered topic and its partitions")
    offsets = frontier[topic]
    if any(
        not isinstance(p, str) or not p.isdigit() or type(o) is not int or o < 0 for p, o in offsets.items()
    ):
        raise ValueError("frontier uses non-negative integer exclusive offsets and partition strings")
    expected = {int(p): o for p, o in offsets.items()}
    if sum(expected.values()) > 20_000:
        raise ValueError("repair exceeds the bounded 20,000-delivery profile")
    actual = {
        p: (count, low, high)
        for p, count, low, high in con.execute(
            'select partition,count(*),min("offset"),max("offset") from stream.delivery '
            "where cluster_id=%s and topic_id=%s group by partition",
            [spec["cluster_id"], spec["topic_id"]],
        )
    }
    if any(p not in expected for p in actual) or any(
        actual.get(p, (0, None, None)) != ((o, 0, o - 1) if o else (0, None, None))
        for p, o in expected.items()
    ):
        raise ValueError(
            "durable deliveries are not the complete zero-based prefix at the requested frontier"
        )
    dataset = Dataset(spec["dataset_id"], utc(spec["source_start"]), utc(spec["source_end"]))
    events, positions, size = {}, {}, 0
    with con.cursor(name="repair_source") as cursor:
        cursor.execute(
            'select key,value from stream.delivery where cluster_id=%s and topic_id=%s order by partition,"offset"',
            [spec["cluster_id"], spec["topic_id"]],
        )
        for key, value in cursor:
            size += len(key or b"") + len(value or b"")
            if size > 64 * 1024 * 1024:
                raise ValueError("repair exceeds the bounded 64 MiB payload profile")
            try:
                event, digest = decode(key, value, dataset)
            except ContractError:
                continue
            eid, position = event["event_id"], (event["payment_id"], event["sequence_no"])
            if (eid in events and events[eid] != (event, digest)) or (
                position in positions and positions[position] != eid
            ):
                raise ValueError("immutable identity conflict in durable source prefix")
            events[eid] = (event, digest)
            positions[position] = eid
    stored = {
        eid: (event, digest)
        for eid, event, digest in con.execute(
            "select event_id,event,semantic_hash from stream.intake_event where dataset_id=%s",
            [dataset.dataset_id],
        )
    }
    if stored != events:
        raise ValueError(
            "canonical intake disagrees with the validated prefix or includes another source epoch"
        )
    return events


def backfill(con, spec: dict, repair_id: str, reason: str) -> tuple[dict, bool]:
    require_stream_database(con)
    if not isinstance(spec, dict) or type(spec.get("version")) is not int or spec["version"] != 1:
        raise ValueError("only integer repair-plan version 1 is supported")
    identifier(repair_id, "repair_id")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
        raise ValueError("a non-empty repair reason of at most 1000 characters is required")
    digest = checksum(dict(plan=spec, reason=reason))
    generation = spec["generation"]
    with con.transaction():
        # Both readers must be paused in this MVP. Locks keep the selected prefix/reference stable.
        lock_writer(con)
        if not con.execute(
            "select pg_try_advisory_xact_lock(hashtextextended(%s,0))",
            ["payrecon/intake/" + spec["dataset_id"]],
        ).fetchone()[0]:
            raise RuntimeError("pause the intake writer before repair")
        previous = con.execute(
            "select request_hash,summary from ops.stream_backfill where repair_id=%s", [repair_id]
        ).fetchone()
        if previous:
            if previous[0] != digest:
                raise ValueError("repair ID was reused with a different request")
            return previous[1], True
        current = _plan(con, generation)
        if checksum(current) != checksum(spec):
            raise ValueError(
                "repair plan is stale or has changed identity, frontier or reference; create a new plan"
            )
        events = _source_events(con, spec)
        existing = {
            eid: (event, digest)
            for eid, event, digest in con.execute(
                "select event_id,event,semantic_hash from stream.live_event where generation=%s",
                [generation],
            )
        }
        if any(events.get(eid) != value for eid, value in existing.items()):
            raise ValueError("admitted live history disagrees with the selected canonical source")
        before = snapshot(con, generation)
        for eid, (event, event_hash) in sorted(events.items()):
            if eid in existing:
                continue
            con.execute(
                "insert into stream.live_event(generation,event_id,payment_id,sequence_no,semantic_hash,event,admission_origin) "
                "values (%s,%s,%s,%s,%s,%s,'backfill')",
                [generation, eid, event["payment_id"], event["sequence_no"], event_hash, Jsonb(event)],
            )
        project(con, generation, {event["payment_id"] for event, _ in events.values()})
        summary = dict(
            added_events=len(events) - len(existing),
            source_events=len(events),
            source_digest=checksum(sorted((eid, h) for eid, (_, h) in events.items())),
            before=before,
            after=snapshot(con, generation),
        )
        con.execute(
            "insert into ops.stream_backfill(repair_id,generation,request_hash,plan,reason,status,summary) "
            "values (%s,%s,%s,%s,%s,'completed',%s)",
            [repair_id, generation, digest, Jsonb(spec), reason, Jsonb(summary)],
        )
    return summary, False
