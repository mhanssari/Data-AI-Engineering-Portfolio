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
        # Everything recon.run_matching reads, so that a trace from a result reaches every source.
        for source in (
            "core.invoice_line", "core.supplier_invoice", "core.po_line", "core.purchase_order", "core.grn_line",
            "core.goods_receipt", "core.purchase_requisition", "core.approval", "core.employee", "core.contract",
            "core.supplier_bank_account", "core.policy_parameter", "core.fx_rate", "mdm.supplier_xref",
        ):  # fmt: skip
            run.lineage(conn, source, "recon.match_result", lines)
        for target, table in (
            ("recon.exception", "exception"),
            ("recon.control_breach", "control_breach"),
            ("recon.grni_line", "grni_line"),
        ):
            count = conn.execute(
                f"select count(*) as n from recon.{table} where run_id = %s", (run.run_id,)
            ).fetchone()["n"]
            run.lineage(
                conn,
                "recon.match_result"
                if table == "exception"
                else "core.purchase_requisition"
                if table == "control_breach"
                else "core.po_line",
                target,
                count,
            )
        run.counts = {
            "recon_run_id": run.run_id,
            "cutoff": cutoff.isoformat(),
            "lines": lines,
            **{row["status_code"]: row["lines"] for row in statuses},
        }
        return run.counts
