"""Thin entry point to the SQL matching engine in sql/routines/30_recon.sql. No business logic lives here."""

from datetime import date

from p2p_recon.db import connect, pipeline_run


def run_matching(cutoff: date) -> dict:
    with pipeline_run("recon", {"cutoff": cutoff}) as run, connect() as conn:
        conn.execute("select recon.run_matching(%s, %s)", (run.run_id, cutoff))
        statuses = conn.execute(
            "select status_code, count(*) as lines from recon.match_result where run_id = %s group by 1 order by 1",
            (run.run_id,),
        ).fetchall()
        lines = sum(row["lines"] for row in statuses)
        run.lineage(conn, "core.invoice_line", "recon.match_result", lines)
        run.counts = {
            "recon_run_id": run.run_id,
            "cutoff": cutoff.isoformat(),
            "lines": lines,
            **{row["status_code"]: row["lines"] for row in statuses},
        }
        return run.counts
