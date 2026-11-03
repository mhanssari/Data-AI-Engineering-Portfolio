import streamlit as st

from p2p_recon.admin import ontology, ui
from p2p_recon.db import connect_as

role = ui.require_reviewer()
meta = ontology.metadata()
class_rows, property_rows, concept_rows, shape_rows = (
    ontology.classes(),
    ontology.properties(),
    ontology.concepts(),
    ontology.shapes(),
)

with connect_as(role) as conn:
    ui.log_view(conn, "ontology")
    cover = ontology.coverage(conn)
    drift = ontology.drift(conn)
    validation = ontology.latest_validation(conn)

    with st.container(horizontal=True):
        st.metric("Ontology version", meta["version"], border=True)
        st.metric("Classes", len(class_rows), border=True)
        st.metric("Properties", len(property_rows), border=True)
        st.metric("Concepts", len(concept_rows), border=True)
        st.metric("Coverage gaps", sum(1 for r in cover["rows"] if r["gap"]), border=True)
    st.caption(f"{meta['label']} · {meta['iri']} · mapping.yaml version {meta['mapping_version']}")
    with st.container(horizontal=True):
        for name, content in ontology.turtle_files().items():
            st.download_button(
                f"Download {name}", content, file_name=name, mime="text/turtle", key=f"onto_download_{name}"
            )

    tabs = st.tabs(
        [
            "Classes",
            "Properties",
            "Vocabularies",
            "Shapes",
            "Coverage",
            "Drift",
            "Validation",
            "Change impact",
        ]
    )
    with tabs[0]:
        st.graphviz_chart(ontology.hierarchy_dot(), width="stretch")
        st.dataframe(
            class_rows, hide_index=True, alt="Ontology classes with parents, definitions and mapped tables"
        )
    with tabs[1]:
        st.dataframe(property_rows, hide_index=True, alt="Ontology properties with their domains and ranges")
    with tabs[2]:
        schemes = sorted({c["scheme"] for c in concept_rows})
        chosen = st.pills("Scheme", schemes, selection_mode="multi", default=schemes, key="onto_schemes")
        st.dataframe(
            [c for c in concept_rows if c["scheme"] in (chosen or schemes)],
            hide_index=True,
            alt="SKOS concepts with English and German labels, definitions and broader terms",
        )
        st.caption("German labels appear only where vocab.ttl gives one; none is translated here.")
    with tabs[3]:
        st.dataframe(
            [r for r in shape_rows if r["constraint"] != "SPARQL"],
            hide_index=True,
            alt="SHACL property constraints by shape",
        )
        st.subheader("SPARQL constraints")
        for row in (r for r in shape_rows if r["constraint"] == "SPARQL"):
            with st.expander(f"{row['shape']}: {row['message']}"):
                st.code(row["sparql"], language="sparql")
    with tabs[4]:
        st.caption(
            f"Rows are counted in PostgreSQL as the reviewer; nodes are what graph sync run {cover['graph_sync_run_id']} "
            "recorded. Bank accounts are counted through the masked view."
        )
        st.dataframe(
            sorted(cover["rows"], key=lambda r: (r["gap"] is None, r["class"])),
            hide_index=True,
            alt="Coverage of each ontology class: table, graph label, row count and node count",
        )
    with tabs[5]:
        st.subheader("Codes in recon that the vocabulary lacks")
        if drift["used_not_in_vocabulary"] or drift["in_sql_not_in_vocabulary"]:
            st.error(
                f"In data: {drift['used_not_in_vocabulary']}. In the matching SQL: {drift['in_sql_not_in_vocabulary']}",
                title="Drift",
            )
        else:
            st.success(
                "Every status and reason code in recon and in the matching SQL is a SKOS concept.",
                title="None",
            )
        left, right = st.columns(2)
        left.subheader("Concepts not used in the latest run")
        left.dataframe(
            [{"concept": c} for c in drift["not_used_in_latest_run"]],
            hide_index=True,
            alt="Status and reason concepts unused in the latest matching run",
        )
        left.caption(f"Never used in any run: {', '.join(drift['never_used']) or 'none'}.")
        right.subheader("Concepts without a definition")
        right.dataframe(
            drift["missing_definition"], hide_index=True, alt="Status and reason concepts with no definition"
        )
        st.subheader("Use in the latest run")
        st.dataframe(drift["usage"], hide_index=True, alt="How many rows carry each status and reason code")
    with tabs[6]:
        if validation["run_id"] is None:
            st.info("No SHACL validation recorded yet. Run `uv run p2p validate-ontology`.")
        else:
            st.caption(
                f"Run {validation['run_id']}. A sampled chain that breaks a control fails here, as SQL holds it; SQL's "
                "reasons are shown beside it so the two can be compared."
            )
            st.dataframe(
                validation["rows"], hide_index=True, alt="SHACL results for the examples and sampled chains"
            )
    with tabs[7]:
        terms = ontology.terms()
        kind, name = st.selectbox(
            "Class, property or concept", terms, format_func=lambda t: f"{t[0]}: {t[1]}", key="onto_term"
        )
        ui.log_view(conn, "ontology", "ontology_term", name)
        st.json(ontology.impact(kind, name))
        st.subheader(f"Review notes on {name}")
        ui.notes_panel(conn, "ontology_term", name, key=f"term-{name}")
