"""Review page: uv run streamlit run src/p2p_recon/review_app.py --server.address 127.0.0.1

The sign-in is simulated: the selector picks a database role, and nothing checks who is using it. What the page then shows
and allows is decided by PostgreSQL for that role, not by this file. Binding to 127.0.0.1 keeps it on this machine.
"""

import streamlit as st

from p2p_recon import graph, review
from p2p_recon.agents.tools import LineKey
from p2p_recon.db import connect_as
from p2p_recon.retrieval import load_user

ROLES = ("ap_analyst_uk", "ap_analyst_de", "controller", "auditor")

st.set_page_config(page_title="Exception review", layout="wide")
st.title("Exception review")
st.caption(
    "Fictional group, synthetic data. Agents propose; you decide. A decision is recorded and nothing is posted or paid. "
    "Status and amounts come from SQL."
)

requested_role = st.query_params.get("user", ROLES[0])
role = st.sidebar.selectbox(
    "Acting as (simulated sign-in)",
    ROLES,
    index=ROLES.index(requested_role) if requested_role in ROLES else 0,
)

with connect_as(role) as conn:
    user = load_user(conn, role)
    st.sidebar.write(f"Companies: {', '.join(user.company_codes)}")
    lines = review.queue(conn)
    st.subheader(f"Queue: {len(lines)} open")
    if not lines:
        st.info(
            "Nothing is waiting for you. Run `uv run p2p agent-run --as <role>` to investigate held lines."
        )
        st.stop()

    st.dataframe(
        [
            {
                "Proposal": line["proposal_id"],
                "Company": line["company_code"],
                "Invoice": line["invoice_id"],
                "Line": line["line_number"],
                "Status": line["sql_status"],
                "Reasons": ", ".join(line["sql_reasons"]),
                "Proposed action": line["proposed_action"],
            }
            for line in lines
        ],
        hide_index=True,
        width="stretch",
    )

    ids = [line["proposal_id"] for line in lines]
    requested = st.query_params.get("proposal")
    start = ids.index(int(requested)) if requested and requested.isdigit() and int(requested) in ids else 0
    proposal_id = st.selectbox("Open proposal", ids, index=start)
    detail = review.proposal_detail(conn, proposal_id)
    proposal, match = detail["proposal"], detail["match"]

    left, right = st.columns([3, 2])
    with left:
        st.subheader(f"Invoice {proposal['invoice_id']}, line {proposal['line_number']}")
        st.markdown(f"**Proposed action:** `{proposal['proposed_action']}`")
        st.markdown(f"**Agent's explanation:** {proposal['explanation']}")
        if proposal["guard_flags"]:
            st.warning("Guard flags: " + ", ".join(proposal["guard_flags"]))

        st.markdown("**From SQL**")
        st.table(
            [{"Measure": name, "Value": str(value)} for name, value in match.items() if value is not None]
        )

        st.markdown("**Citations**")
        for citation in detail["citations"]:
            if citation["kind"] == "document":
                with st.expander(f"Document: {citation['reference']}"):
                    if citation["content"] is None:
                        st.write("You do not have access to this document.")
                    else:
                        st.caption(citation["header"])
                        # Third-party text, so it is shown as plain text and never rendered as markdown.
                        st.text(citation["content"])
            else:
                with st.expander(f"Row: {citation['reference']}"):
                    st.json(citation["key"])
                    if citation["row"] is None:
                        st.write("You do not have access to this row, or it no longer exists.")
                    else:
                        st.json({k: str(v) for k, v in citation["row"].items()})

    with right:
        st.subheader("Graph path")
        key = LineKey(proposal["source_system"], proposal["invoice_id"], proposal["line_number"])
        try:
            with graph.driver() as db:
                dot = review.graph_path_dot(user, db, key)
        except Exception as exc:  # The graph is a convenience. A person can still decide without it.
            dot = None
            st.warning(f"The graph is unavailable: {type(exc).__name__}")
        if dot:
            st.graphviz_chart(dot)
        else:
            st.write("No graph path available.")

        st.subheader("Decision")
        if role == "auditor":
            st.info("Auditors have read-only access, so you cannot record a decision.")
        elif review.needs_controller(proposal) and role != "controller":
            st.info("A failed control goes to the controller, so you cannot record a decision on this one.")
        else:
            note = st.text_area("Note (required for an override)", key=f"note-{proposal_id}")
            accept, override, query = st.columns(3)
            pressed = None
            if accept.button("Accept proposal", key="accept"):
                pressed = "ACCEPT"
            if override.button("Override", key="override"):
                pressed = "OVERRIDE"
            if query.button("Query", key="query"):
                pressed = "QUERY"
            if pressed:
                try:
                    decision_id = review.decide(conn, proposal_id, pressed, note)
                    st.success(
                        f"Recorded decision {decision_id}: {pressed}. Nothing has been posted or paid."
                    )
                except review.DecisionRefused as exc:
                    st.error(str(exc))
