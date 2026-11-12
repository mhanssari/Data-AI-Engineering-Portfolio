"""Reported progress/frontier diagnostics. Sink commits and the two readers remain independent."""

import json

from psycopg.types.json import Jsonb


def record_progress(con, generation: str, role: str, progress: dict) -> None:
    progress = json.loads(json.dumps(progress, default=str))
    con.execute(
        "insert into ops.stream_progress(generation,query_role,batch_id,progress) "
        "select %s,%s,%s,%s where exists(select 1 from ops.stream_batch "
        "where generation=%s and query_role=%s and batch_id=%s) on conflict do nothing",
        [generation, role, progress["batchId"], Jsonb(progress), generation, role, progress["batchId"]],
    )


def status(con, generation: str) -> dict:
    with con.transaction():
        con.execute("set transaction isolation level repeatable read, read only")
        return _status(con, generation)


def _status(con, generation: str) -> dict:
    source = con.execute(
        "select dataset_id,cluster_id,topic_id from stream.lineage where generation=%s and query_role='live'",
        [generation],
    ).fetchone()
    if source is None:
        raise ValueError("select a registered live generation")
    intake = con.execute(
        "select generation from stream.lineage where dataset_id=%s and cluster_id=%s and topic_id=%s "
        "and query_role='intake' order by created_at desc limit 1",
        source,
    ).fetchone()

    def frontier(gen, role):
        if gen is None:
            return None
        row = con.execute(
            "select progress from ops.stream_progress where generation=%s and query_role=%s "
            "order by batch_id desc limit 1",
            [gen, role],
        ).fetchone()
        sources = row[0].get("sources", []) if row else []
        end = sources[0].get("endOffset") if len(sources) == 1 else None
        return json.loads(end) if isinstance(end, str) else end

    intake_end = frontier(intake[0] if intake else None, "intake")
    live_end = frontier(generation, "live")

    def reported_through_commit(gen, role):
        if gen is None:
            return False
        committed, reported = con.execute(
            "select (select max(batch_id) from ops.stream_batch where generation=%s and query_role=%s),"
            "(select max(batch_id) from ops.stream_progress where generation=%s and query_role=%s)",
            [gen, role, gen, role],
        ).fetchone()
        return committed is not None and committed == reported

    progress_covers_commits = reported_through_commit(generation, "live") and reported_through_commit(
        intake[0] if intake else None, "intake"
    )
    conflicts = con.execute(
        "select (select count(*) from stream.quarantine where blocking) "
        "+ (select count(*) from stream.live_conflict where generation=%s)",
        [generation],
    ).fetchone()[0]
    unmatched = con.execute(
        "select count(*) from stream.live_event l left join stream.intake_event i "
        "on i.dataset_id=%s and i.event_id=l.event_id and i.semantic_hash=l.semantic_hash "
        "where l.generation=%s and i.event_id is null",
        [source[0], generation],
    ).fetchone()[0]
    missing = con.execute(
        "select count(*) from stream.intake_event i where i.dataset_id=%s and not exists "
        "(select 1 from stream.live_event l where l.generation=%s and l.event_id=i.event_id)",
        [source[0], generation],
    ).fetchone()[0]
    diagnostics = dict(
        con.execute(
            "select outcome,count(*) from stream.event_application where generation=%s group by outcome",
            [generation],
        ).fetchall()
    )
    reference = con.execute("select as_of_arrival from stream.reference_snapshot").fetchone()
    repair = con.execute(
        "select repair_id,completed_at,plan from ops.stream_backfill where generation=%s "
        "order by completed_at desc,repair_id desc limit 1",
        [generation],
    ).fetchone()
    return dict(
        generation=str(generation),
        intake_frontier=intake_end,
        live_frontier=live_end,
        consistent_at_recorded_frontier=bool(
            intake_end
            and live_end == intake_end
            and progress_covers_commits
            and not conflicts
            and not unmatched
            and not missing
            and not diagnostics.get("unknown_reference")
            and not diagnostics.get("identity_mismatch")
            and reference
        ),
        blocking_conflicts=conflicts,
        progress_covers_commits=progress_covers_commits,
        live_events_unverified_by_intake=unmatched,
        intake_events_not_admitted=missing,
        diagnostics=diagnostics,
        reference_arrival=str(reference[0]) if reference else None,
        last_repair=(
            dict(
                repair_id=repair[0],
                completed_at=repair[1].isoformat(),
                frontier=repair[2]["frontier"],
                reference=repair[2]["reference"],
            )
            if repair
            else None
        ),
        scope="recorded source frontiers only; broker head may have advanced; live view may need late repair",
    )
