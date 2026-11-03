"""Dataset lineage from ops.lineage_edge, and data contracts: their versions, the last recorded check and what changed
since each contract's last release.

Contracts keep only the last released version in contracts/released, so the change history shown is current against
that release, not every version ever published. Git holds the rest.
"""

import psycopg

from p2p_recon import contracts


def lineage_runs(conn: psycopg.Connection, limit: int = 50) -> list[dict]:
    """Pipeline runs that recorded lineage, newest first."""
    return conn.execute(
        """
        select r.run_id, r.step, r.status, r.started_at, count(e.*) as edges
        from ops.pipeline_run r join ops.lineage_edge e using (run_id)
        group by r.run_id order by r.run_id desc limit %s
        """,
        (limit,),
    ).fetchall()


def edges(conn: psycopg.Connection, run_id: int | None = None) -> list[dict]:
    """One run's edges, or, with no run, the latest recorded movement for each pair of datasets: the current picture."""
    if run_id is not None:
        return conn.execute(
            """
            select e.source_dataset, e.target_dataset, e.row_count, e.run_id, r.step
            from ops.lineage_edge e join ops.pipeline_run r using (run_id)
            where e.run_id = %s order by 1, 2
            """,
            (run_id,),
        ).fetchall()
    return conn.execute(
        """
        select distinct on (e.source_dataset, e.target_dataset)
               e.source_dataset, e.target_dataset, e.row_count, e.run_id, r.step
        from ops.lineage_edge e join ops.pipeline_run r using (run_id)
        order by e.source_dataset, e.target_dataset, e.run_id desc
        """
    ).fetchall()


def _quote(text: object) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def lineage_dot(rows: list[dict]) -> str:
    """Datasets as boxes grouped by schema, edges labelled with the step and rows moved."""
    datasets = sorted({r["source_dataset"] for r in rows} | {r["target_dataset"] for r in rows})
    schemas: dict[str, list[str]] = {}
    for name in datasets:
        schemas.setdefault(name.split(".", 1)[0] if "." in name else name, []).append(name)
    lines = [
        "digraph lineage {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded", fontsize=10];',
        "  edge [fontsize=9];",
    ]
    for index, (schema, members) in enumerate(sorted(schemas.items())):
        lines.append(f"  subgraph cluster_{index} {{ label={_quote(schema)}; style=dashed;")
        lines += [f"    {_quote(m)};" for m in members]
        lines.append("  }")
    for r in rows:
        label = f"{r['step']} · {r['row_count']:,}"
        lines.append(
            f"  {_quote(r['source_dataset'])} -> {_quote(r['target_dataset'])} [label={_quote(label)}];"
        )
    lines.append("}")
    return "\n".join(lines)


def last_check(conn: psycopg.Connection) -> dict:
    """The last recorded contracts-check: one row per contract with its problems."""
    run = conn.execute(
        """
        select run_id, started_at, parameters->>'as_of' as as_of from ops.pipeline_run
        where step = 'contracts_check' and status = 'succeeded' order by run_id desc limit 1
        """
    ).fetchone()
    if run is None:
        return {"run": None, "rows": {}}
    rows = conn.execute(
        "select check_name, scope as version, actual as problems, status, detail from ops.dq_result where run_id = %s",
        (run["run_id"],),
    ).fetchall()
    return {"run": run, "rows": {r["check_name"].removeprefix("contract."): r for r in rows}}


def contract_overview(conn: psycopg.Connection) -> list[dict]:
    """Each contract: version against its release, what would break a consumer, and the last check's verdict."""
    check = last_check(conn)
    rows = []
    for name, contract in contracts.load_contracts().items():
        released_path = contracts.RELEASED_DIR / f"{name}.yaml"
        released = contracts.load_contract(released_path) if released_path.exists() else None
        recorded = check["rows"].get(name)
        rows.append(
            {
                "contract": name,
                "version": contract["version"],
                "released_version": released["version"] if released else None,
                "owner": contract["owner"],
                "consumers": ", ".join(contract["consumers"]),
                "tables": ", ".join(contract["tables"]),
                "changed_since_release": released is not None and released != contract,
                "breaking_changes": "; ".join(contracts.breaking_changes(released, contract))
                if released
                else None,
                "last_check": recorded["status"] if recorded else None,
                "problems": recorded["detail"] if recorded else None,
            }
        )
    return rows
