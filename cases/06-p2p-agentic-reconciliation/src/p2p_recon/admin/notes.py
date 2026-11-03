"""Review notes and the admin view log, the reviewer's only writes.

Both are append-only in the database. A follow-up is a new note that points at the earlier one; nothing is edited.
"""

import psycopg

ROLE = "admin_reviewer"
OBJECT_TYPES = (
    "agent_run",
    "proposal",
    "golden_supplier",
    "ontology_term",
    "rule",
    "contract",
    "pipeline_run",
)
CATEGORIES = (
    "data_quality",
    "agent_behaviour",
    "citation",
    "entity_resolution",
    "ontology_gap",
    "control_design",
    "access",
    "other",
)


class NoteRefused(Exception):
    """The database, or a rule on what a note must contain, refused it."""


def add_note(
    conn: psycopg.Connection,
    object_type: str,
    object_id: str,
    category: str,
    note: str,
    follow_up: bool = False,
    follows_up: int | None = None,
) -> int:
    """Written as the role in use: the column grant leaves out written_by and written_at, so neither can be supplied."""
    if object_type not in OBJECT_TYPES:
        raise NoteRefused(f"Object type must be one of {', '.join(OBJECT_TYPES)}")
    if category not in CATEGORIES:
        raise NoteRefused(f"Category must be one of {', '.join(CATEGORIES)}")
    if not note.strip() or not str(object_id).strip():
        raise NoteRefused("A note needs an object and some text")
    try:
        return conn.execute(
            """
            insert into agent.admin_review_note (object_type, object_id, category, note, follow_up, follows_up)
            values (%s, %s, %s, %s, %s, %s) returning note_id
            """,
            (object_type, str(object_id), category, note.strip(), follow_up, follows_up),
        ).fetchone()["note_id"]
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ForeignKeyViolation) as exc:
        raise NoteRefused(f"Not permitted: {exc.diag.message_primary}") from exc


def notes(
    conn: psycopg.Connection, object_type: str | None = None, object_id: str | None = None
) -> list[dict]:
    """Newest first. A note is open while it asks for a follow-up and nothing follows it up."""
    return conn.execute(
        """
        select n.note_id, n.written_by, n.written_at, n.object_type, n.object_id, n.category, n.note, n.follow_up,
               n.follows_up,
               n.follow_up and not exists (
                   select 1 from agent.admin_review_note later where later.follows_up = n.note_id) as open
        from agent.admin_review_note n
        where (%(type)s::text is null or n.object_type = %(type)s) and (%(id)s::text is null or n.object_id = %(id)s)
        order by n.note_id desc
        """,
        {"type": object_type, "id": None if object_id is None else str(object_id)},
    ).fetchall()


def log_view(
    conn: psycopg.Connection,
    page: str,
    object_type: str | None = None,
    object_id: str | None = None,
) -> int:
    """Application-level logging: it records what the admin app showed, not every read the role could make."""
    return conn.execute(
        "select ops.log_admin_view(%s, %s, %s) as view_id",
        (page, object_type, None if object_id is None else str(object_id)),
    ).fetchone()["view_id"]
