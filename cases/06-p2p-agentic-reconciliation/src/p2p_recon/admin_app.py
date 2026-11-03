"""Governance review: uv run streamlit run src/p2p_recon/admin_app.py --server.address 127.0.0.1

Read-only oversight across both companies for `admin_reviewer`. The sign-in is simulated, as on the review page, and every
page refuses any other role. What the reviewer may read and write is decided by PostgreSQL (migration 007), not here.
Binding to 127.0.0.1 keeps it on this machine.
"""

import streamlit as st

from p2p_recon.admin import ui

st.set_page_config(page_title="Governance review", layout="wide")
ui.sign_in()

page = st.navigation(
    [
        st.Page("admin_pages/overview.py", title="Overview", icon=":material/dashboard:", default=True),
        st.Page("admin_pages/agents.py", title="Agents", icon=":material/smart_toy:"),
        st.Page("admin_pages/evaluations.py", title="Evaluations", icon=":material/fact_check:"),
        st.Page("admin_pages/entities.py", title="Entities and relationships", icon=":material/hub:"),
        st.Page("admin_pages/ontology.py", title="Ontology", icon=":material/account_tree:"),
        st.Page("admin_pages/lineage.py", title="Lineage and contracts", icon=":material/timeline:"),
        st.Page("admin_pages/access.py", title="Access and audit", icon=":material/admin_panel_settings:"),
        st.Page("admin_pages/rules.py", title="How the agents decide", icon=":material/rule:"),
    ],
    position="top",
)
st.title(page.title)
page.run()
