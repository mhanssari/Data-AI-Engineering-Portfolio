import streamlit as st

from p2p_recon import contracts
from p2p_recon.admin import overview, ui
from p2p_recon.db import connect_as

role = ui.require_reviewer()

with connect_as(role) as conn:
    ui.log_view(conn, "overview")
    alerts = overview.alerts(conn)
    dq = overview.latest_dq(conn)
    graph = overview.graph_reconciliation(conn)
    fresh = overview.freshness(conn)
    quarantine = overview.open_quarantine(conn)
    held = overview.held_by_currency(conn)
    queue = overview.review_queue(conn)
    onto = overview.ontology_status(conn)
    runs = overview.recent_runs(conn)

if not alerts:
    st.success("No threshold in alerts.yaml is breached by the stored figures.", title="No alerts")
for alert in alerts:
    show = st.error if alert.severity == "error" else st.warning
    show(f"{alert.description}. Actual {alert.actual}, limit {alert.limit}.", title=alert.name)

with st.container(horizontal=True):
    st.metric("Data-quality checks passed", dq["counts"]["pass"], border=True)
    st.metric("Warnings", dq["counts"]["warn"], border=True)
    st.metric("Failures", dq["counts"]["fail"], border=True)
    st.metric("Graph mismatches", graph["mismatches"], border=True)
    st.metric("Open quarantine", sum(r["n"] for r in quarantine), border=True)
    st.metric("Open review queue", sum(r["n"] for r in queue["open_by_action"]), border=True)

left, right = st.columns(2)
with left:
    st.subheader("Held lines and value on hold")
    st.caption("Per company and currency. Amounts in different currencies are never added together.")
    st.dataframe(held, hide_index=True, alt="Held invoice lines and their value, per company and currency")

    st.subheader("Freshness")
    st.caption(
        f"Hours since each contract's newest source extract, at the reference clock {contracts.REFERENCE_CLOCK:%Y-%m-%d %H:%M} UTC. "
        "The synthetic batches have fixed dates, so the wall clock would make every source stale."
    )
    st.dataframe(fresh, hide_index=True, alt="Freshness of each data contract against its SLA")

    st.subheader("Review queue")
    queue_left, queue_right = st.columns(2)
    queue_left.dataframe(queue["open_by_action"], hide_index=True, alt="Open proposals by proposed action")
    queue_right.dataframe(
        queue["decided"], hide_index=True, alt="Latest decision on each decided proposal, by kind"
    )

with right:
    st.subheader("Ontology")
    if onto["shacl_run_id"] is None:
        st.write(f"Version {onto['version']}. No SHACL validation has been recorded yet.")
    else:
        st.write(
            f"Version {onto['version']}. Last SHACL validation (run {onto['shacl_run_id']}): "
            f"{onto['conforming']} of {onto['checked']} sampled invoice chains conform. "
            "A chain that breaks a control does not conform, as SQL holds it."
        )

    st.subheader("Graph against PostgreSQL")
    st.caption(f"As recorded by graph sync run {graph['run_id']}: expected is PostgreSQL, actual is Neo4j.")
    st.dataframe(graph["rows"], hide_index=True, alt="Node and relationship counts, PostgreSQL against Neo4j")

    if dq["not_passing"]:
        st.subheader("Checks not passing")
        st.dataframe(dq["not_passing"], hide_index=True, alt="Data-quality checks that warned or failed")

st.subheader("Latest pipeline runs")
st.dataframe(runs, hide_index=True, alt="The most recent pipeline runs and their status")
if quarantine:
    st.subheader("Open quarantine")
    st.dataframe(quarantine, hide_index=True, alt="Raw rows still in quarantine, by entity and reason")
