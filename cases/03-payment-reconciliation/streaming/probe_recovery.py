"""Read actual DB/checkpoint facts after a forced crash or repair; no Spark process starts here."""

import argparse
import hashlib
import json
from pathlib import Path

from payrecon import db
from payrecon.streaming.progress import status
from payrecon.streaming.recovery import snapshot

parser = argparse.ArgumentParser()
parser.add_argument("--checkpoint", type=Path, required=True)
parser.add_argument("--parity", action="store_true")
args = parser.parse_args()
with db.connect() as con:
    generation = str(
        con.execute("select generation from stream.lineage where query_role='live'").fetchone()[0]
    )
    result = dict(state=status(con, generation), business=snapshot(con, generation))
    result["batches"] = [
        dict(batch_id=b, input_hash=h, summary=s, committed_at=str(t))
        for b, h, s, t in con.execute(
            "select batch_id,input_hash,summary,committed_at from ops.stream_batch where generation=%s and query_role='live' order by batch_id",
            [generation],
        )
    ]
    result["commits"] = sorted(
        int(p.name) for p in (args.checkpoint / "commits").glob("*") if p.name.isdigit()
    )
    result["offsets"] = sorted(
        int(p.name) for p in (args.checkpoint / "offsets").glob("*") if p.name.isdigit()
    )
    result["query_id"] = json.loads((args.checkpoint / "metadata").read_text())["id"]
    digest = hashlib.sha256()
    for path in sorted(p for p in args.checkpoint.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(args.checkpoint)).encode())
        digest.update(path.read_bytes())
    marker = args.checkpoint.with_name(args.checkpoint.name + ".lineage.json")
    digest.update(marker.read_bytes())
    result["checkpoint_hash"] = digest.hexdigest()
    result["repairs"] = con.execute("select count(*) from ops.stream_backfill").fetchone()[0]
    if args.parity:
        from reference_parity import compare

        result["batch_parity"] = compare(con, generation)
    print(json.dumps(result, sort_keys=True))
