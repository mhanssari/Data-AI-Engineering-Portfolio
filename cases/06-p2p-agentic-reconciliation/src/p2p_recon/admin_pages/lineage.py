import streamlit as st

from p2p_recon.admin import datasets, ui
from p2p_recon.db import connect_as

role = ui.require_reviewer()

with connect_as(role) as conn:
    ui.log_view(conn, "lineage")

    st.header("Dataset lineage")
    runs = datasets.lineage_runs(conn)
    run_id = st.selectbox(
        "Pipeline run",
        [None, *[r["run_id"] for r in runs]],
        format_func=lambda v: (
            "latest movement per dataset pair"
            if v is None
            else next(
                f"run {v} · {r['step']} · {r['started_at']:%Y-%m-%d %H:%M}" for r in runs if r["run_id"] == v
            )
        ),
        key="lineage_run",
    )
    edges = datasets.edges(conn, run_id)
    if not edges:
        st.info("No lineage recorded yet. Run `uv run p2p run-all`.")
    else:
        st.graphviz_chart(datasets.lineage_dot(edges), width="stretch")
        st.caption(
            "Run-level lineage: datasets and rows moved per step. Row-level lineage is in each proposal's trace."
        )
        st.dataframe(edges, hide_index=True, alt="Lineage edges: source, target, rows moved and step")
    if run_id is not None:
        ui.log_view(conn, "lineage", "pipeline_run", run_id)
        st.subheader(f"Review notes on run {run_id}")
        ui.notes_panel(conn, "pipeline_run", run_id, key=f"run-{run_id}")

    st.header("Data contracts")
    check = datasets.last_check(conn)
    if check["run"] is None:
        st.info("No contracts check recorded yet. Run `uv run p2p contracts-check`.")
    else:
        st.caption(
            f"Last check: run {check['run']['run_id']}, {check['run']['started_at']:%Y-%m-%d %H:%M}, freshness judged at "
            f"{check['run']['as_of']}."
        )
    overview = datasets.contract_overview(conn)
    st.dataframe(overview, hide_index=True, alt="Each contract's version, release, consumers and last check")
    st.caption(
        "Only the last released version of each contract is kept, in contracts/released; breaking changes are shown "
        "against it. Earlier versions are in Git."
    )
    name = st.selectbox("Contract", [c["contract"] for c in overview], key="lineage_contract")
    if name:
        ui.log_view(conn, "lineage", "contract", name)
        st.subheader(f"Review notes on the {name} contract")
        ui.notes_panel(conn, "contract", name, key=f"contract-{name}")
