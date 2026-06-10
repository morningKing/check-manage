"""DB layer for ai_chat_batches and their child ai_chat_sessions rows.

Routes are thin; this module owns the SQL.
"""
import uuid
from psycopg2.extras import RealDictCursor

from db import get_db


MAX_FILES_PER_BATCH = 50


def create_batch(user_id: str, *, name: str, prompt: str,
                 template_id: str | None, files: list[dict],
                 scan_task_id: str | None = None,
                 agent: str | None = None) -> dict:
    """Atomically insert a batch + N child sessions.

    `files` is a list of {name, path} dicts where `path` is workspace-relative
    (under batch-staging/...). Each entry may also carry an optional `recordId`
    key: the source record id, stamped into `source_record_id` for scan tasks.
    `scan_task_id` is an optional param linking the child sessions to a scan task.
    `agent` is an optional OpenCode agent name to use for this batch.
    Returns {batch, sessions}.
    """
    if not files:
        raise ValueError("at least one file required")
    if len(files) > MAX_FILES_PER_BATCH:
        raise ValueError(f"max {MAX_FILES_PER_BATCH} files per batch")

    batch_id = str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO ai_chat_batches "
                "  (id, user_id, name, prompt, template_id, total, status, agent) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s) RETURNING *",
                (batch_id, user_id, name, prompt, template_id, len(files), agent),
            )
            batch = dict(cur.fetchone())

            sessions = []
            for seq, f in enumerate(files):
                sid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO ai_chat_sessions "
                    "  (id, user_id, status, batch_id, batch_seq, batch_input_file, "
                    "   scan_task_id, source_record_id) "
                    "VALUES (%s, %s, 'pending', %s, %s, %s, %s, %s) RETURNING *",
                    (sid, user_id, batch_id, seq, f['path'],
                     scan_task_id, f.get('recordId')),
                )
                sessions.append(dict(cur.fetchone()))
        conn.commit()
    return {'batch': batch, 'sessions': sessions}


def list_batches(user_id: str, *, page: int, page_size: int) -> dict:
    offset = (page - 1) * page_size
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM ai_chat_batches WHERE user_id = %s "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (user_id, page_size, offset),
            )
            items = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT count(*) AS n FROM ai_chat_batches WHERE user_id = %s",
                        (user_id,))
            total = cur.fetchone()['n']
    return {'items': items, 'total': total, 'page': page, 'pageSize': page_size}


def get_batch_detail(user_id: str, batch_id: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM ai_chat_batches WHERE id=%s AND user_id=%s",
                        (batch_id, user_id))
            batch = cur.fetchone()
            if not batch:
                return None
            cur.execute(
                "SELECT id, status, batch_seq, batch_input_file, "
                "       opencode_session_id, error_message, last_message_preview "
                "FROM ai_chat_sessions WHERE batch_id=%s ORDER BY batch_seq",
                (batch_id,),
            )
            sessions = [dict(r) for r in cur.fetchall()]
    return {'batch': dict(batch), 'sessions': sessions}


def delete_batch(user_id: str, batch_id: str) -> bool:
    """Returns True if deleted, False if not found.

    Callers MUST run per-session workspace cleanup BEFORE invoking this for
    children that have a workspace_path. See routes for the orchestration.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_chat_batches WHERE id=%s AND user_id=%s",
                        (batch_id, user_id))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def reset_failed_to_pending(user_id: str, batch_id: str) -> int:
    """Returns count of sessions reset. Also clears batch.failed counter and
    recomputes batch.status."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_chat_sessions "
                "SET status='pending', error_message=NULL "
                "WHERE batch_id=%s AND status='failed' "
                "  AND batch_id IN (SELECT id FROM ai_chat_batches WHERE user_id=%s)",
                (batch_id, user_id),
            )
            count = cur.rowcount
            if count:
                cur.execute(
                    "UPDATE ai_chat_batches SET failed = failed - %s, "
                    "  status = CASE WHEN done = total THEN 'completed' "
                    "                ELSE 'pending' END "
                    "WHERE id = %s",
                    (count, batch_id),
                )
            conn.commit()
    return count
