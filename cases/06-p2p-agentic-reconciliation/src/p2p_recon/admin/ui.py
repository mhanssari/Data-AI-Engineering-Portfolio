"""Streamlit helpers shared by the admin pages: simulated sign-in, refusal of other roles, view logging and notes.

Query logic does not belong here; it lives in the other modules of this package, where it is tested without Streamlit.
Nothing role-dependent is cached: each rerun opens a connection as the selected role, so one role's rows can never be
served to another from a cache.
"""

import psycopg
import streamlit as st

from p2p_recon.admin import notes

REVIEWER = notes.ROLE
# Other roles are offered so that the refusal can be seen; PostgreSQL would refuse most of the reads anyway.
ROLES = (REVIEWER, "auditor", "controller", "ap_analyst_uk", "ap_analyst_de")


def sign_in() -> str:
    """The sidebar selector stands in for an identity provider; ?user= preselects it, as on the review page."""
    requested = st.query_params.get("user", REVIEWER)
    role = st.sidebar.selectbox(
        "Acting as (simulated sign-in)",
        ROLES,
        index=ROLES.index(requested) if requested in ROLES else 0,
        key="admin_role",
    )
    st.sidebar.caption(
        "Fictional group, synthetic data. Read-only oversight: nothing here posts, pays or decides."
    )
    return role


def require_reviewer() -> str:
    """Every page calls this first. A refusal stops the page before any query runs."""
    role = st.session_state.get("admin_role", REVIEWER)
    if role != REVIEWER:
        st.error(
            f"`{role}` cannot open the governance pages. They are for `{REVIEWER}` only.",
            title="Not permitted",
        )
        st.stop()
    return role


def log_view(
    conn: psycopg.Connection, page: str, object_type: str | None = None, object_id: object = None
) -> None:
    """Once per page and object per session, so that a widget rerun is not recorded as a fresh view."""
    seen = st.session_state.setdefault("admin_views_logged", set())
    marker = (page, object_type, None if object_id is None else str(object_id))
    if marker not in seen:
        notes.log_view(conn, page, object_type, object_id)
        seen.add(marker)


def notes_panel(conn: psycopg.Connection, object_type: str, object_id: object, key: str) -> None:
    """Notes already written on one object, and a form for a new note or a follow-up. Nothing is ever edited."""
    existing = notes.notes(conn, object_type, str(object_id))
    for note in existing:
        status = "open follow-up" if note["open"] else ("follow-up closed" if note["follow_up"] else "note")
        st.caption(
            f"#{note['note_id']} · {note['written_at']:%Y-%m-%d %H:%M} · {note['written_by']} · "
            f"{note['category']} · {status}"
            + (f" · follows #{note['follows_up']}" if note["follows_up"] else "")
        )
        st.text(note["note"])
    with st.form(f"note-{key}", clear_on_submit=True):
        category = st.selectbox("Finding", notes.CATEGORIES, key=f"note-category-{key}")
        text = st.text_area("Note", key=f"note-text-{key}")
        follow_up = st.checkbox("Needs a follow-up", key=f"note-follow-{key}")
        open_ids = [n["note_id"] for n in existing if n["open"]]
        follows = st.selectbox(
            "Follows up note",
            [None, *open_ids],
            key=f"note-follows-{key}",
            format_func=lambda v: "none" if v is None else f"#{v}",
        )
        if st.form_submit_button("Add note"):
            try:
                note_id = notes.add_note(
                    conn, object_type, str(object_id), category, text, follow_up, follows
                )
                st.success(f"Recorded note #{note_id}. Notes cannot be edited; add a follow-up instead.")
            except notes.NoteRefused as exc:
                st.error(str(exc))
