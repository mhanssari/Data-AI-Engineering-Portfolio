"""A compact snapshot for the continuous A12 checker; never starts Spark."""

import json
from pathlib import Path

from payrecon import db
from payrecon.streaming.recovery import snapshot

with db.connect() as con, con.transaction():
    con.execute("set transaction isolation level repeatable read, read only")
    row = con.execute(
        "select generation,checkpoint_path from stream.lineage where query_role='live'"
    ).fetchone()
    if row is None:
        print(json.dumps(dict(ready=False)))
    else:
        generation, checkpoint = row
        reports = [
            p
            for (p,) in con.execute(
                "select progress from ops.stream_progress where generation=%s and query_role='live' order by batch_id",
                [generation],
            )
        ]
        state = [p for p in reports if p.get("stateOperators")]
        if not state:
            print(json.dumps(dict(ready=False)))
        else:
            progress = state[-1]
            assert len(progress["stateOperators"]) == 1, "expected one stateful operator"
            operator = progress["stateOperators"][0]
            sources = progress["sources"]
            assert len(sources) == 1, "expected one Kafka source"
            end = sources[0]["endOffset"]
            query_id = json.loads((Path(checkpoint) / "metadata").read_text())["id"]
            result = dict(
                ready=True,
                generation=str(generation),
                query_id=query_id,
                run_id=str(progress["runId"]),
                batch_id=progress["batchId"],
                frontier=json.loads(end) if isinstance(end, str) else end,
                watermark=progress.get("eventTime", {}).get("watermark"),
                rows=operator["numRowsTotal"],
                memory_bytes=operator["memoryUsedBytes"],
                removed_total=sum(s.get("numRowsRemoved", 0) for p in state for s in p["stateOperators"]),
                dropped_total=sum(
                    s.get("numRowsDroppedByWatermark", 0) for p in state for s in p["stateOperators"]
                ),
                duplicate_total=sum(
                    s.get("customMetrics", {}).get("numDroppedDuplicateRows", 0)
                    for p in state
                    for s in p["stateOperators"]
                ),
                max_observed_rows=max(s["numRowsTotal"] for p in state for s in p["stateOperators"]),
                max_observed_memory_bytes=max(
                    s["memoryUsedBytes"] for p in state for s in p["stateOperators"]
                ),
                business=snapshot(con, str(generation)),
                deliveries=con.execute("select count(*) from stream.delivery").fetchone()[0],
                intake_events=con.execute("select count(*) from stream.intake_event").fetchone()[0],
                counts=con.execute(
                    "select count(*),coalesce(sum(authorised),0),coalesce(sum(declined),0),coalesce(sum(expired),0),min(decline_rate_pct),max(decline_rate_pct) from stream.operation_minute where generation=%s",
                    [generation],
                ).fetchone(),
            )
            print(json.dumps(result, default=str, sort_keys=True))
