import streamlit as st

from p2p_recon.admin import access, agents, audit_pack, evaluations, notes, ui
from p2p_recon.db import connect_as

role = ui.require_reviewer()

with connect_as(role) as conn:
    ui.log_view(conn, "access")
    tabs = st.tabs(["Permissions", "Audit log", "Access tests", "View log", "Review notes", "Audit pack"])

    with tabs[0]:
        schemas = st.pills(
            "Schemas",
            access.SCHEMAS,
            selection_mode="multi",
            default=["core", "agent", "ops"],
            key="access_schemas",
        )
        st.caption(
            "Read from the PostgreSQL catalogue: S select, I insert, U update, D delete, including privileges through "
            "role membership. Row-level security then narrows what a select returns."
        )
        st.dataframe(
            access.permission_matrix(conn, tuple(schemas or access.SCHEMAS)),
            hide_index=True,
            alt="Privileges per role on each table and view, with row-level security and policies",
        )
        st.subheader("Column grants")
        st.dataframe(
            access.column_grants(conn), hide_index=True, alt="Who may read the bank account number column"
        )
        st.subheader("Row-level security policies")
        st.dataframe(
            access.policies(conn),
            hide_index=True,
            alt="Every row-level security policy as PostgreSQL holds it",
        )

    with tabs[1]:
        with st.container(horizontal=True, vertical_alignment="bottom"):
            events = st.multiselect("Event", access.audit_events(conn), key="audit_events")
            roles = st.multiselect("Role in use", access.ROLES, key="audit_roles")
            dates = st.date_input("Between", value=(), key="audit_dates")
            proposal = st.number_input("Proposal", min_value=0, value=0, step=1, key="audit_proposal")
        date_from, date_to = (tuple(dates) + (None, None))[:2] if dates else (None, None)
        st.dataframe(
            access.audit_log(
                conn, tuple(events), tuple(roles), date_from, date_to or date_from, proposal or None
            ),
            hide_index=True,
            alt="Audit log entries matching the filter, newest first",
        )
        st.caption("Written only by triggers; no role can insert, change or remove an entry.")

    with tabs[2]:
        result = evaluations.access_results(conn)
        if result["run"] is None:
            st.info("No access probe recorded yet. Run `uv run p2p access-probe`.")
        else:
            st.caption(
                f"Access probe recorded {result['run']['recorded_at']:%Y-%m-%d %H:%M}: {result['passed']} attempts as "
                f"expected, {result['failed']} not. Each attempt ran as the role, in a transaction that was rolled back."
            )
            st.dataframe(
                result["rows"], hide_index=True, alt="Each attempt by each role, expected and actual outcome"
            )

    with tabs[3]:
        st.caption(
            "Application-level: it records what this app showed. Anyone able to run SQL as the reviewer could read "
            "without being logged here."
        )
        st.dataframe(access.view_log(conn), hide_index=True, alt="Admin page and object views, newest first")

    with tabs[4]:
        written = notes.notes(conn)
        st.caption(
            f"{len(written)} notes, {sum(n['open'] for n in written)} open follow-ups. Notes cannot be edited."
        )
        st.dataframe(written, hide_index=True, alt="Every review note, newest first")
        st.subheader("Add a note on any object")
        with st.container(horizontal=True, vertical_alignment="bottom"):
            object_type = st.selectbox("Object type", notes.OBJECT_TYPES, key="access_note_type")
            object_id = st.text_input("Object id", key="access_note_id")
        if object_id.strip():
            ui.notes_panel(conn, object_type, object_id.strip(), key=f"any-{object_type}-{object_id.strip()}")

    with tabs[5]:
        st.caption(
            "Proposals, decisions, notes and data-quality status for agent runs started in a date range."
        )
        with st.container(horizontal=True, vertical_alignment="bottom"):
            pack_dates = st.date_input("Runs started between", value=(), key="pack_dates")
            include_evaluation = st.toggle("Include evaluation runs", key="pack_evaluation")
        pack_from, pack_to = (tuple(pack_dates) + (None, None))[:2] if pack_dates else (None, None)
        run_filter = agents.RunFilter(
            date_from=pack_from, date_to=pack_to or pack_from, include_evaluation=include_evaluation
        )
        pack = audit_pack.range_pack(conn, run_filter)
        name = audit_pack.default_name("audit-pack", pack_from, pack_to or pack_from)
        with st.container(horizontal=True):
            st.download_button(
                "Download markdown",
                pack["markdown"],
                file_name=f"{name}.md",
                mime="text/markdown",
                key="pack_md",
            )
            st.download_button(
                "Download CSV", pack["csv"], file_name=f"{name}.csv", mime="text/csv", key="pack_csv"
            )
