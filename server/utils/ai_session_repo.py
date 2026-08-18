"""DB layer for standalone /v1/ai-sessions rows (routes/open_api_ai_sessions.py).

A standalone session is an ai_chat_sessions row with batch_id=NULL and
api_key_id set. It's executed by the exact same worker/dispatcher as batch
children — see utils/batch_engine.py's `if batch_id is None:` branch in
`_run_one` — not a separate execution path. This module only owns the two
queries the external API needs: create + owner-scoped read.
"""
import uuid

from psycopg2.extras import RealDictCursor

from db import get_db
from utils.batch_repo import _text_from_content


def create_session(user_id: str, *, prompt: str,
                   agent: str | None = None,
                   model: str | None = None,
                   title: str | None = None,
                   api_key_id: str | None = None) -> dict:
    """Insert a pending standalone session; the worker picks it up on its next
    dispatch tick.

    `prompt` is stashed in `continue_prompt` — batch_engine.py's `_run_one`
    reads it on first claim (the `batch_id is None` branch) and clears it
    immediately, exactly like the existing "continue an existing session"
    flow already does with that same column.
    """
    sid = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO ai_chat_sessions "
                "  (id, user_id, title, status, continue_prompt, agent, model, api_key_id) "
                "VALUES (%s, %s, %s, 'pending', %s, %s, %s, %s) RETURNING *",
                (sid, user_id, title or '新会话', prompt, agent, model, api_key_id),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return row


def get_session_for_owner(session_id: str, user_id: str, api_key_id: str) -> dict | None:
    """Single-row lookup with double ownership check (user_id + api_key_id —
    same tightening pattern as batch_repo.get_batch_detail).

    `output` is populated only when status == 'completed' — same safety gate
    as batch_repo.get_batch_results: while running, or after a mid-run
    timeout that got marked failed, ai_chat_messages may already hold a
    half-written assistant turn; returning that as `output` would hand
    truncated text to an integration reading only the terminal state.
    """
    sql = """
        SELECT s.id, s.title, s.status, s.agent, s.model, s.error_message,
               s.created_at, s.last_active_at,
               (SELECT m.content FROM ai_chat_messages m
                 WHERE m.session_id = s.id AND m.role = 'assistant'
                 ORDER BY m.seq DESC LIMIT 1) AS content
          FROM ai_chat_sessions s
         WHERE s.id = %s AND s.user_id = %s AND s.api_key_id = %s
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (session_id, user_id, api_key_id))
            row = cur.fetchone()
    if not row:
        return None
    row = dict(row)
    completed = row['status'] == 'completed'
    return {
        'id': row['id'],
        'title': row['title'],
        'status': row['status'],
        'agent': row['agent'],
        'model': row['model'],
        'createdAt': row['created_at'],
        'lastActiveAt': row['last_active_at'],
        'output': _text_from_content(row.get('content')) if completed else None,
        'error': row.get('error_message'),
    }
