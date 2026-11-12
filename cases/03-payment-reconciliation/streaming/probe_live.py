"""Business-state snapshot and actual progress diagnostics for the isolated live check."""

import json

from payrecon import db
from payrecon.streaming.progress import status

with db.connect() as con:
    generation = con.execute("select generation from stream.lineage where query_role='live'").fetchone()[0]
    result = status(con, str(generation))
    result["fingerprints"] = {
        table: con.execute(
            f"select md5(coalesce(string_agg(t::text,'|' order by t::text),'')) from {table} t where generation=%s",
            [generation],
        ).fetchone()[0]
        for table in (
            "stream.live_event",
            "stream.event_application",
            "stream.payment_latest",
            "stream.event_minute",
        )
    }
    result["counts"] = {
        table: con.execute(f"select count(*) from {table}").fetchone()[0]
        for table in ("stream.delivery", "stream.intake_event", "stream.quarantine", "stream.live_event")
    }
    result["payments"] = {
        p: dict(status=s, held=h, captured=c)
        for p, s, h, c in con.execute(
            "select payment_id,status,held_events,captured_minor from stream.payment_latest where generation=%s",
            [generation],
        )
    }
    result["minutes"] = [
        dict(
            zip(
                ("minute", "merchant", "currency", "authorised", "declined", "expired", "decline_rate_pct"),
                row,
                strict=True,
            )
        )
        for row in con.execute(
            "select to_char(minute_start at time zone 'UTC','YYYY-MM-DD HH24:MI'),merchant_id,currency,authorised,declined,expired,decline_rate_pct from stream.operation_minute where generation=%s order by 1,2,3",
            [generation],
        )
    ]
    progress = [
        p
        for (p,) in con.execute(
            "select progress from ops.stream_progress where query_role='live' order by batch_id"
        )
    ]
    result["watermarks"] = [
        p["eventTime"]["watermark"] for p in progress if p.get("eventTime", {}).get("watermark")
    ]
    result["state_operators"] = [s for p in progress for s in p.get("stateOperators", [])]
    print(json.dumps(result, default=str, sort_keys=True))
