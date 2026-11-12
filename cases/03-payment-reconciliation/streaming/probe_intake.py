"""Integration-check snapshot/cleanup, run inside the optional case container; no Spark startup."""

import argparse
import json
import os
import re
import shutil
from pathlib import Path

from psycopg import sql

from payrecon import db

parser = argparse.ArgumentParser()
parser.add_argument("--cleanup-token")
args = parser.parse_args()
if args.cleanup_token:
    token = args.cleanup_token
    if (
        not re.fullmatch(r"[a-f0-9]{12}", token)
        or os.environ.get("PGDATABASE") != f"payments_stream_check_{token}"
    ):
        raise SystemExit("cleanup refuses a non-test database/token")
    with db.connect("postgres") as con:
        con.execute(
            sql.SQL("drop database if exists {} with (force)").format(
                sql.Identifier(os.environ["PGDATABASE"])
            )
        )
    checkpoint = Path("/state") / f"check-{token}"
    if checkpoint.resolve().parent != Path("/state").resolve():
        raise SystemExit("checkpoint cleanup is outside /state")
    if checkpoint.exists():
        shutil.rmtree(checkpoint)
    checkpoint.with_name(checkpoint.name + ".lineage.json").unlink(missing_ok=True)
    print("isolated intake-check database/checkpoint removed")
else:
    with db.connect() as con:
        counts = {
            table: con.execute(f"select count(*) from {table}").fetchone()[0]
            for table in ("stream.delivery", "stream.intake_event", "stream.quarantine", "stream.lineage")
        }
        counts["blocking"] = con.execute("select count(*) from stream.quarantine where blocking").fetchone()[
            0
        ]
        counts["late_events"] = con.execute(
            "select count(*) from stream.intake_event where "
            "(event->>'ingestion_time')::timestamptz - (event->>'event_time')::timestamptz "
            "> interval '10 minutes'"
        ).fetchone()[0]
        counts["canonical_digest"] = con.execute(
            "select md5(string_agg(t::text,'|' order by event_id)) from stream.intake_event t"
        ).fetchone()[0]
        print(json.dumps(counts, sort_keys=True))
