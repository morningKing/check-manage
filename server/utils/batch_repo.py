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
                 agent: str | None = None,
                 model: str | None = None,
                 provision_repo: str | None = None,
                 provision_ref: str | None = None) -> dict:
    """Atomically insert a batch + N child sessions.

    `files` is a list of {name, path} dicts where `path` is workspace-relative
    (under batch-staging/...). Each entry may also carry an optional `recordId`
    key: the source record id, stamped into `source_record_id` for scan tasks.
    `scan_task_id` is an optional param linking the child sessions to a scan task.
    `agent` is an optional OpenCode agent name to use for this batch.
    `model` is an optional "<providerID>/<modelID>" to run this batch with;
    empty falls back to the global OPENCODE_MODEL / the agent's default.
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
                "  (id, user_id, name, prompt, template_id, total, status, agent, model, "
                "   provision_repo, provision_ref) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s) RETURNING *",
                (batch_id, user_id, name, prompt, template_id, len(files), agent, model,
                 provision_repo, provision_ref),
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


def append_to_batch(user_id: str, batch_id: str, files: list[dict]) -> dict | None:
    """Append N child sessions to an existing batch (any status). seq continues
    from max+1, total += N, status recomputed (-> running). Returns
    {batch, sessions} or None if the batch isn't found / not owned."""
    if not files:
        raise ValueError("at least one file required")
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT total FROM ai_chat_batches WHERE id=%s AND user_id=%s",
                        (batch_id, user_id))
            row = cur.fetchone()
            if not row:
                return None
            if row['total'] + len(files) > MAX_FILES_PER_BATCH:
                raise ValueError(f"max {MAX_FILES_PER_BATCH} files per batch")
            cur.execute("SELECT COALESCE(MAX(batch_seq), -1) AS m "
                        "FROM ai_chat_sessions WHERE batch_id=%s", (batch_id,))
            start = cur.fetchone()['m'] + 1
            sessions = []
            for i, f in enumerate(files):
                sid = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO ai_chat_sessions "
                    "  (id, user_id, status, batch_id, batch_seq, batch_input_file) "
                    "VALUES (%s, %s, 'pending', %s, %s, %s) RETURNING *",
                    (sid, user_id, batch_id, start + i, f['path']),
                )
                sessions.append(dict(cur.fetchone()))
            cur.execute("UPDATE ai_chat_batches SET total = total + %s WHERE id=%s",
                        (len(files), batch_id))
        conn.commit()
    _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id)


def _recompute_batch_status_for(batch_id: str) -> None:
    """Local SQL equivalent of batch_engine._recompute_batch_status to avoid
    circular imports (batch_engine imports batch_repo)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT done, failed, total FROM ai_chat_batches WHERE id=%s", (batch_id,))
            row = cur.fetchone()
            if not row:
                return
            done, failed, total = row
            terminal = done + failed
            status = ('pending' if terminal == 0 else
                      'running' if terminal < total else
                      'failed' if failed == total else
                      'completed' if done == total else 'partial')
            cur.execute("UPDATE ai_chat_batches SET status=%s, "
                        "completed_at = CASE WHEN %s = total THEN now() ELSE NULL END "
                        "WHERE id=%s", (status, terminal, batch_id))
        conn.commit()


def reexecute_child(user_id: str, batch_id: str, session_id: str) -> dict | None:
    """Re-run a single TERMINAL (completed/failed) batch child from scratch:
    delete its old messages, reset it to pending with a cleared OpenCode session,
    roll back the batch counter, recompute status (-> running). Returns updated
    detail, or None if the child isn't found / not owned. Raises ValueError if the
    child is not in a terminal state."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.status FROM ai_chat_sessions s "
                "JOIN ai_chat_batches b ON s.batch_id = b.id "
                "WHERE s.id = %s AND s.batch_id = %s AND b.user_id = %s",
                (session_id, batch_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            status = row[0]
            if status not in ('completed', 'failed'):
                raise ValueError('only completed/failed children can be re-executed')
            cur.execute("DELETE FROM ai_chat_messages WHERE session_id = %s", (session_id,))
            cur.execute(
                "UPDATE ai_chat_sessions SET status='pending', opencode_session_id=NULL, "
                "  last_message_preview=NULL, error_message=NULL WHERE id = %s",
                (session_id,),
            )
            if status == 'completed':
                cur.execute("UPDATE ai_chat_batches SET done = done - 1 WHERE id = %s", (batch_id,))
            else:
                cur.execute("UPDATE ai_chat_batches SET failed = failed - 1 WHERE id = %s", (batch_id,))
        conn.commit()
    _recompute_batch_status_for(batch_id)
    return get_batch_detail(user_id, batch_id)


def update_batch_config(user_id: str, batch_id: str, *,
                        agent: str | None, model: str | None,
                        provision_repo: str | None = None,
                        provision_ref: str | None = None) -> dict | None:
    """Update a batch's agent/model/provision repo (owner-only). NULL clears to
    the default. Returns updated detail, or None if not found / not owned. Takes
    effect on the next run the worker claims (retry / reexecute / pending), since
    the worker reads these fresh per run via _fetch_batch_context."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ai_chat_batches SET agent = %s, model = %s, "
                "  provision_repo = %s, provision_ref = %s "
                "WHERE id = %s AND user_id = %s",
                (agent, model, provision_repo, provision_ref, batch_id, user_id),
            )
            updated = cur.rowcount > 0
        conn.commit()
    if not updated:
        return None
    return get_batch_detail(user_id, batch_id)


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
