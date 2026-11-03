import streamlit as st
from neo4j.exceptions import ServiceUnavailable

from p2p_recon import graph
from p2p_recon.admin import entities, ui
from p2p_recon.db import connect_as

role = ui.require_reviewer()

with connect_as(role) as conn:
    ui.log_view(conn, "entities")
    scope = entities.reviewer_scope(conn)
    st.caption(
        f"Scope: companies {', '.join(scope.company_codes)}, source systems {', '.join(scope.source_systems)}. "
        "Neo4j Community Edition has no role-based access, so the scope is applied in each graph query."
    )

    st.header("Neighbourhood")
    with st.container(horizontal=True, vertical_alignment="bottom"):
        kind = st.selectbox("Find a", list(entities.SEARCHABLE), key="entities_kind")
        text = st.text_input("Number, id or name contains", key="entities_text")
    matches = entities.search(conn, kind, text)
    if not matches:
        st.info("Nothing you can see matches.")
    else:
        chosen = st.selectbox(
            "Result", matches, format_func=lambda m: f"{m['name']} ({m['key']})", key="entities_result"
        )
        with st.container(horizontal=True, vertical_alignment="bottom"):
            depth = st.slider("Depth", 1, entities.MAX_DEPTH, 1, key="entities_depth")
            keep_labels = st.multiselect(
                "Labels", entities.labels(), key="entities_labels", placeholder="All labels"
            )
            keep_types = st.multiselect(
                "Relationship types",
                entities.relationship_types(),
                key="entities_types",
                placeholder="All types",
            )
        ui.log_view(conn, "entities", chosen["label"], chosen["key"])
        try:
            with graph.driver() as db:
                around = entities.neighbourhood(
                    db, scope, chosen["label"], chosen["key"], depth, keep_labels, keep_types
                )
                gaps = entities.chain_gaps(db, scope)
        except ServiceUnavailable:
            around, gaps = None, None
            st.warning(
                "Neo4j is not reachable. Start the containers and run `uv run p2p graph-sync`.",
                title="No graph",
            )
        if around is not None:
            if not around["found"]:
                st.info("This record is not in the graph. Run `uv run p2p graph-sync` if it was added since.")
            else:
                if around["truncated"]:
                    st.warning(
                        f"Showing the first {entities.MAX_PATHS} paths only. Reduce the depth or the labels."
                    )
                st.graphviz_chart(entities.neighbourhood_dot(around), width="stretch")
                st.dataframe(
                    [
                        {
                            "label": n["label"],
                            "key": n["key"],
                            **{k: str(v) for k, v in n["properties"].items()},
                        }
                        for n in around["nodes"].values()
                    ],
                    hide_index=True,
                    alt="Properties of each node in the neighbourhood, with bank details masked",
                )
        if gaps is not None:
            st.header("Chain-completeness gaps")
            st.caption("A gap is not always an error: an invoice with no receipt may simply be early.")
            left, right = st.columns([1, 2])
            left.dataframe(gaps["counts"], hide_index=True, alt="Chain gaps by type")
            right.dataframe(gaps["rows"], hide_index=True, alt="Documents with a chain gap")

    st.header("Supplier resolution")
    golden = entities.golden_suppliers(conn)
    left, right = st.columns([2, 3])
    with left:
        st.dataframe(golden, hide_index=True, alt="Golden supplier records, largest clusters first")
    with right:
        gid = st.selectbox(
            "Golden supplier",
            [g["supplier_gid"] for g in golden],
            format_func=lambda g: next(
                f"{r['golden_name']} ({g}, {r['member_count']})" for r in golden if r["supplier_gid"] == g
            ),
            key="entities_golden",
        )
        if gid:
            ui.log_view(conn, "entities", "golden_supplier", gid)
            detail = entities.golden_detail(conn, gid)
            st.subheader("Members")
            st.dataframe(
                detail["members"],
                hide_index=True,
                alt="Source supplier records resolved to this golden record",
            )
            st.subheader("Candidate pairs")
            st.dataframe(
                detail["pairs"], hide_index=True, alt="Scored candidate pairs touching this supplier"
            )
            if detail["steward_decisions"]:
                st.subheader("Steward decisions")
                st.dataframe(
                    detail["steward_decisions"], hide_index=True, alt="Steward decisions on these pairs"
                )
            st.subheader("Review notes")
            ui.notes_panel(conn, "golden_supplier", gid, key=f"golden-{gid}")
    st.subheader("Steward queue")
    st.caption(
        "Pairs sent to a steward. A recorded decision takes effect on the next `resolve-suppliers` run."
    )
    st.dataframe(
        entities.steward_queue(conn), hide_index=True, alt="Supplier pairs awaiting or decided by a steward"
    )
