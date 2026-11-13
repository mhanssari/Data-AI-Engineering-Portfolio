"""Inspect the late-file recovery after Airflow has run 22 and 23 October.

Run with the case Python inside Airflow. --save-rerun-baseline records result digests and landing counts;
after clearing and rerunning the 23rd, --check-rerun proves unchanged data and an additional execution.
No schemas, files or results are deleted by this verifier.
"""

import argparse
import json
import os
from pathlib import Path

from payrecon import db

BASELINE = Path("/opt/airflow/payment_reconciliation/a6-baseline.json")
parser = argparse.ArgumentParser()
mode = parser.add_mutually_exclusive_group()
mode.add_argument("--save-rerun-baseline", action="store_true")
mode.add_argument("--check-rerun", action="store_true")
args = parser.parse_args()
for setting in ("HOST", "PORT", "DATABASE", "USER", "PASSWORD"):
    os.environ[f"PG{setting}"] = os.environ[f"CASE03_PG{setting}"]

with db.connect() as con:
    completed = {
        row[0].isoformat()
        for row in con.execute(
            "select distinct as_of_arrival from ops.run where status = 'succeeded' "
            "and as_of_arrival in ('2026-10-22', '2026-10-23')"
        ).fetchall()
    }
    if completed != {"2026-10-22", "2026-10-23"}:
        raise SystemExit("Wait for both Airflow arrival runs to succeed before running this verifier")
    for arrival, expected in (
        ("2026-10-22", {"missing_settlement": 473, "pending": 2}),
        ("2026-10-23", {"matched": 475}),
    ):
        actual = dict(
            con.execute(
                "select status, count(*) from recon.payment_settlement where as_of_arrival = %s "
                "and currency = 'USD' and due_date = '2026-10-20' group by status",
                [arrival],
            ).fetchall()
        )
        assert actual == expected, (arrival, actual)
        print(f"{arrival}: USD due 20 October: {actual}")
    assert (
        con.execute("select count(*) from recon.control_total where unexplained_minor <> 0").fetchone()[0]
        == 0
    )
    assert (
        con.execute("select count(*) from recon.exception where as_of_arrival = '2026-10-23'").fetchone()[0]
        == 7
    )
    digests = {
        table: con.execute(
            f"select md5(coalesce(string_agg(t::text, '|' order by t::text), '')) from {table} t "
            "where as_of_arrival = '2026-10-23'"
        ).fetchone()[0]
        for table in (
            "recon.payment_settlement",
            "recon.unmatched_line",
            "recon.exception",
            "recon.control_total",
        )
    }
    counts = {
        table: con.execute(f"select count(*) from {table}").fetchone()[0]
        for table in ("ops.file", "raw.payment_event", "raw.settlement_line", "raw.merchant")
    }
    executions = con.execute(
        "select count(*) from ops.run where as_of_arrival = '2026-10-23' and status = 'succeeded'"
    ).fetchone()[0]
    current = {"digests": digests, "landing_counts": counts, "executions": executions}
    if args.save_rerun_baseline:
        BASELINE.write_text(json.dumps(current, indent=2))
        print(f"Saved rerun baseline: {counts}; {executions} successful execution(s)")
    if args.check_rerun:
        before = json.loads(BASELINE.read_text())
        assert digests == before["digests"], "reconciliation results changed on rerun"
        assert counts == before["landing_counts"], "landing counts changed on rerun"
        assert executions > before["executions"], "the rerun did not execute"
        print(
            f"Rerun: identical results and landing counts; executions {before['executions']} -> {executions}"
        )
    print("A6 verified: P14 resolved, 7 planted exceptions remain, all control differences zero")
