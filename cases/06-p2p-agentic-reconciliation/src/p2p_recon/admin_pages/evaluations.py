import streamlit as st

from p2p_recon.admin import evaluations, ui
from p2p_recon.db import connect_as

role = ui.require_reviewer()


def recorded(run: dict | None, command: str) -> bool:
    if run is None:
        st.info(f"Nothing recorded yet. Run `uv run p2p {command}`.")
        return False
    st.caption(f"Evaluation run {run['evaluation_run_id']}, recorded {run['recorded_at']:%Y-%m-%d %H:%M}.")
    return True


with connect_as(role) as conn:
    ui.log_view(conn, "evaluations")
    providers = evaluations.providers(conn)
    provider = st.selectbox("Agent evaluation provider", providers or [None], key="evaluations_provider")
    matrix = evaluations.scenario_matrix(conn, provider)
    checks = evaluations.robustness(conn, provider)
    access = evaluations.access_results(conn)
    found = evaluations.retrieval(conn)

    summary = matrix["summary"]
    with st.container(horizontal=True):
        st.metric(
            "Planted lines passing",
            f"{sum(r['passed'] for r in matrix['rows'])} of {len(matrix['rows'])}",
            border=True,
        )
        st.metric("False auto-approvals", int(summary.get("false_auto_approvals", 0)), border=True)
        st.metric(
            "Robustness checks passing",
            f"{sum(bool(r['passed']) for r in checks if r['passed'] is not None)} of {sum(r['passed'] is not None for r in checks)}",
            border=True,
        )
        st.metric(
            "Access attempts as expected",
            f"{access['passed']} of {access['passed'] + access['failed']}",
            border=True,
        )
    if provider == "offline":
        st.caption(
            "The offline provider is a rule table written by the author of the labels: its action accuracy shows the "
            "pipeline works, not that a model judges well. Hostile, outage and access results do not depend on it."
        )

    st.header("Planted scenarios")
    if recorded(matrix["run"], "agent-eval"):
        st.dataframe(
            matrix["rows"],
            hide_index=True,
            alt="Pass or fail for each planted invoice line, with failed checks",
        )

    st.header("Hostile provider, outage and access")
    if checks:
        st.dataframe(
            checks, hide_index=True, alt="Hostile, outage and cross-company results with what had to hold"
        )

    st.header("Access probe")
    if recorded(access["run"], "access-probe"):
        if access["failed"]:
            st.error(f"{access['failed']} attempts did not behave as expected.", title="Access surprises")
        st.dataframe(access["rows"], hide_index=True, alt="Each role's attempts, expected and actual outcome")

    st.header("Retrieval")
    if recorded(found["run"], "eval-retrieval"):
        st.dataframe(
            found["methods"], hide_index=True, alt="Recall at k, MRR and latency per retrieval method"
        )
        left, right = st.columns(2)
        left.dataframe(found["by_kind"], hide_index=True, alt="Retrieval scores by kind of question")
        right.dataframe(
            found["questions"],
            hide_index=True,
            alt="Rank of the first expected chunk per question and method",
        )
        st.caption(
            "The labels are a small hand-written set by the corpus's author, so read differences as indicative."
        )

    st.header("Trend")
    left, right = st.columns(2)
    for column, kind in ((left, "agents"), (right, "retrieval")):
        rows = evaluations.trend(conn, kind)
        column.subheader(kind.capitalize())
        if len({r["evaluation_run_id"] for r in rows}) < 2:
            column.caption("Fewer than two recorded runs: nothing to trend yet.")
            column.dataframe(rows, hide_index=True, alt=f"Recorded {kind} figures")
        else:
            column.line_chart(
                [
                    {"run": r["evaluation_run_id"], "series": r["series"], "value": float(r["value"])}
                    for r in rows
                ],
                x="run",
                y="value",
                color="series",
            )
