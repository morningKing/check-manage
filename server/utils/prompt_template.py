"""CRUD helpers for ai_chat_prompt_templates.

Owned by routes/ai_chat_prompt_templates.py but kept here for testability and
re-use by routes/ai_chat_batches.py (which optionally records template_id).
"""
import uuid
from psycopg2.errors import UniqueViolation
from psycopg2.extras import RealDictCursor

from db import get_db


class DuplicateTemplateName(ValueError):
    """User tried to create a template with a name they already used."""


def _row(cur):
    """RealDictCursor → plain dict (no metaclass surprises)."""
    r = cur.fetchone()
    return dict(r) if r else None


def create_template(user_id: str, *, name: str, content: str) -> dict:
    new_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute(
                    "INSERT INTO ai_chat_prompt_templates (id, user_id, name, content) "
                    "VALUES (%s, %s, %s, %s) RETURNING *",
                    (new_id, user_id, name, content),
                )
                row = _row(cur)
                conn.commit()
                return row
            except UniqueViolation:
                conn.rollback()
                raise DuplicateTemplateName(name)


def list_templates(user_id: str) -> list[dict]:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM ai_chat_prompt_templates "
                "WHERE user_id = %s ORDER BY updated_at DESC",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_template(user_id: str, template_id: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM ai_chat_prompt_templates "
                "WHERE user_id = %s AND id = %s",
                (user_id, template_id),
            )
            return _row(cur)


def update_template(user_id: str, template_id: str, *,
                    name: str, content: str) -> dict | None:
    """Returns the updated row, or None if the template isn't this user's."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute(
                    "UPDATE ai_chat_prompt_templates "
                    "SET name = %s, content = %s, updated_at = now() "
                    "WHERE id = %s AND user_id = %s RETURNING *",
                    (name, content, template_id, user_id),
                )
                row = _row(cur)
                conn.commit()
                return row
            except UniqueViolation:
                conn.rollback()
                raise DuplicateTemplateName(name)


def delete_template(user_id: str, template_id: str) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ai_chat_prompt_templates "
                "WHERE id = %s AND user_id = %s",
                (template_id, user_id),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
