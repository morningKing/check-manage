"""In-process worker that turns batch child sessions into running OpenCode runs.

Singleton via `get_worker()`. Started from app.py next to existing schedulers,
guarded by WERKZEUG_RUN_MAIN to avoid double-start under Flask's reloader.

OpenCode API adaptation
-----------------------
The project's OpenCodeClient (utils/opencode_client.py) is an HTTP wrapper class.
The plan called for module-level free functions (create_session, send_message,
list_messages) — those don't exist. Instead we keep a module-level client
singleton and expose three thin wrappers:

  opencode_client.create_session(directory)  → str (OC session id)
  opencode_client.send_message(oc_sid, prompt, directory)  → None
  opencode_client.list_messages(oc_sid, directory)  → list[dict]
      (polls SSE events; returns a synthetic list once session.idle fires)

Tests monkeypatch the `opencode_client` name at module level (eng.opencode_client)
so all three calls resolve through the patched object.
"""
import os
import queue
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from psycopg2.extras import RealDictCursor

from db import get_db
from utils.workspace import create_session_workspace


# ---------------------------------------------------------------------------
# Module-level OpenCode client façade
# ---------------------------------------------------------------------------
# Imported as a module so tests can monkeypatch:
#     monkeypatch.setattr(eng, 'opencode_client', fake_oc)
# ---------------------------------------------------------------------------

class _OpenCodeFacade:
    """Thin wrappers over OpenCodeClient that present the API shape the worker
    needs.  A single long-lived instance is created at module import time.

    All three methods are designed so that a MagicMock can replace `opencode_client`
    wholesale in unit tests.
    """

    def _client(self):
        from utils.opencode_client import OpenCodeClient
        from config import OPENCODE_BASE_URL
        return OpenCodeClient(OPENCODE_BASE_URL)

    def create_session(self, directory: str, title: str = '') -> str:
        """Create an OpenCode session bound to `directory`; return its id."""
        return self._client().create_session(directory=directory, title=title)

    def send_message(self, oc_session_id: str, prompt: str,
                     directory: str = '') -> dict:
        """Fire the prompt asynchronously.  Returns a stub dict so callers can
        discard the return value — the real work happens on the SSE stream.
        """
        from config import OPENCODE_MODEL
        self._client().send_prompt_async(
            oc_session_id, prompt,
            model=OPENCODE_MODEL,
            directory=directory,
        )
        return {'id': oc_session_id}

    def list_messages(self, oc_session_id: str,
                      directory: str = '') -> list:
        """Subscribe to the directory SSE stream and collect all message.part
        events until session.idle fires, then return a synthetic message list.

        Returns a list where the last element is an assistant message dict with
        the shape the worker's _await_finished() expects:
            {'role': 'assistant', 'finished': True/False, 'content': [...]}

        Raises on connection errors (propagates to _mark_failed).
        Times out after 5 seconds of no events — the caller (POLL_INTERVAL_SEC
        loop) retries immediately, so a short blocking-read timeout is fine.
        """
        client = self._client()
        result_q: queue.Queue = queue.Queue()

        def _stream():
            try:
                parts_text: list[str] = []
                finished = False
                for evt in client.subscribe_events(directory=directory):
                    etype = evt.get('event', '')
                    props = evt.get('data', {}).get('properties', {}) if evt.get('data') else {}

                    if etype == 'message.part.updated':
                        part = props.get('part') or {}
                        if part.get('type') == 'text' and part.get('text'):
                            parts_text.append(part['text'])
                    elif etype == 'session.idle':
                        finished = True
                        result_q.put(('ok', parts_text, finished))
                        return
            except Exception as exc:
                result_q.put(('err', exc))

        t = threading.Thread(target=_stream, daemon=True)
        t.start()
        t.join(timeout=5)   # short poll window — caller loops

        if result_q.empty():
            # No session.idle yet — return an "not yet finished" synthetic message.
            return [{'role': 'assistant', 'finished': False, 'content': []}]

        item = result_q.get()
        if item[0] == 'err':
            raise item[1]
        _, parts_text, finished = item
        content = [{'type': 'text', 'text': t} for t in parts_text if t]
        return [{'role': 'assistant', 'finished': finished, 'content': content}]


# The module-level name that tests monkeypatch.
opencode_client = _OpenCodeFacade()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

_WORKER = None


def get_worker() -> 'BatchWorker':
    global _WORKER
    if _WORKER is None:
        _WORKER = BatchWorker()
    return _WORKER


def _workspace_root() -> str:
    return os.environ.get('AI_CHAT_WORKSPACE_ROOT', 'ai-workspaces')


def _prepare_workspace(user_id: str, session_id: str,
                       staged_file_path: str) -> str:
    """Create the per-session workspace and copy the staged file into uploads/.

    Returns the absolute workspace path.  Pure side-effect — no DB writes.
    Can be monkeypatched in tests:
        monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))
    """
    ws = create_session_workspace(_workspace_root(), user_id, session_id)
    src = Path(_workspace_root()) / staged_file_path
    up = Path(ws) / 'uploads'
    up.mkdir(parents=True, exist_ok=True)
    # On Windows, copying a just-created staging dir can intermittently raise
    # PermissionError (antivirus / handle-settling contention). Retry a few times.
    last_err = None
    for _attempt in range(3):
        try:
            if src.is_dir():
                # scan-task context directory: copy its whole contents into uploads/
                shutil.copytree(str(src), str(up), dirs_exist_ok=True)
            elif src.exists():
                dst = up / Path(staged_file_path).name
                shutil.copy2(str(src), str(dst))
            last_err = None
            break
        except (PermissionError, OSError) as e:
            last_err = e
            time.sleep(0.3)
    if last_err is not None:
        raise last_err
    return ws


def _recompute_batch_status(batch_id: str) -> None:
    """Set ai_chat_batches.status based on its done/failed/total counts.

    Called from _mark_done and _mark_failed — always within a committed state.
    Exported at module level so tests can call it directly.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT done, failed, total FROM ai_chat_batches WHERE id = %s",
                (batch_id,),
            )
            row = cur.fetchone()
            if not row:
                return
            done, failed, total = row
            terminal = done + failed
            if terminal == 0:
                new_status = 'pending'
            elif terminal < total:
                new_status = 'running'
            elif failed == total:
                new_status = 'failed'
            elif done == total:
                new_status = 'completed'
            else:
                new_status = 'partial'
            cur.execute(
                "UPDATE ai_chat_batches "
                "SET status = %s, "
                "    completed_at = CASE WHEN %s = total THEN now() ELSE NULL END "
                "WHERE id = %s",
                (new_status, terminal, batch_id),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# BatchWorker
# ---------------------------------------------------------------------------

class _SessionTimeout(Exception):
    def __init__(self, seconds: int):
        super().__init__(f'timeout after {seconds}s')
        self.seconds = seconds


class BatchWorker:
    MAX_CONCURRENT = 3
    POLL_INTERVAL_SEC = 2
    SESSION_TIMEOUT_SEC = 1800

    def __init__(self):
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=self.MAX_CONCURRENT)
        self._running_session_ids: set = set()
        self._lock = threading.Lock()
        self._dispatcher: threading.Thread | None = None

    # --- lifecycle ---

    def start(self):
        if self._dispatcher and self._dispatcher.is_alive():
            return
        self._restart_audit()
        self._dispatcher = threading.Thread(
            target=self._dispatcher_loop, daemon=True, name='batch-worker')
        self._dispatcher.start()

    def stop(self, *, wait: bool = True, timeout: float = 5.0):
        """Stop the dispatcher and (optionally) wait for it + the executor.

        Without `wait`, the dispatcher receives the stop signal but its daemon
        thread may still be inside a DB poll cycle when this returns — that
        leaks into subsequent tests under pytest because the next claim picks
        up rows the next test just seeded. Default to waiting so tests behave.
        """
        self._stop.set()
        self._wake.set()
        if not wait:
            return
        if self._dispatcher and self._dispatcher.is_alive():
            self._dispatcher.join(timeout=timeout)
        # cancel_futures so pending submissions don't keep the threadpool alive
        self._executor.shutdown(wait=True, cancel_futures=True)
        # Allow a follow-on start() to spin up a fresh executor.
        self._executor = ThreadPoolExecutor(max_workers=self.MAX_CONCURRENT)

    def notify(self):
        self._wake.set()

    # --- dispatcher ---

    def _dispatcher_loop(self):
        while not self._stop.is_set():
            self._wake.wait(timeout=10)
            self._wake.clear()
            if self._stop.is_set():
                break
            with self._lock:
                free = self.MAX_CONCURRENT - len(self._running_session_ids)
            if free <= 0:
                continue
            pending = self._claim_pending_sessions(limit=free)
            for s in pending:
                with self._lock:
                    self._running_session_ids.add(s['id'])
                self._executor.submit(self._safe_run_one, s)

    def _safe_run_one(self, session_row):
        try:
            self._run_one(session_row)
        except Exception:
            traceback.print_exc()
        finally:
            with self._lock:
                self._running_session_ids.discard(session_row['id'])
            self.notify()  # let the dispatcher start the next queued one

    # --- DB primitives ---

    def _claim_pending_sessions(self, limit: int) -> list:
        if limit <= 0:
            return []
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "WITH picked AS ( "
                    "  SELECT id FROM ai_chat_sessions "
                    "   WHERE status = 'pending' AND batch_id IS NOT NULL "
                    "   ORDER BY created_at, batch_seq "
                    "   FOR UPDATE SKIP LOCKED LIMIT %s "
                    ") "
                    "UPDATE ai_chat_sessions s SET status = 'running' "
                    "FROM picked WHERE s.id = picked.id "
                    "RETURNING s.*",
                    (limit,),
                )
                rows = [dict(r) for r in cur.fetchall()]
            conn.commit()
        return rows

    def _restart_audit(self):
        """Reset any 'running' batch session left over from a previous Flask
        process back to 'pending'.  Idempotent."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET status = 'pending' "
                    "WHERE status = 'running' AND batch_id IS NOT NULL"
                )
            conn.commit()

    # --- per-session run ---

    def _run_one(self, session_row):
        sid = session_row['id']
        user_id = session_row['user_id']
        batch_id = session_row['batch_id']
        prompt = self._fetch_batch_prompt(batch_id)
        if prompt is None:
            # Batch was deleted between claim and prompt fetch.
            # FK CASCADE has already removed our session row; nothing to mark.
            # Scan-task children are intentionally NOT notified via _notify_scan on
            # this path: recovery is handled by the orphan sweep (running rows with
            # no live session get reset to pending).
            return

        try:
            ws = _prepare_workspace(user_id, sid, session_row['batch_input_file'] or '')
            oc_session_id = opencode_client.create_session(directory=ws)
            self._set_opencode_id(sid, oc_session_id)
            opencode_client.send_message(oc_session_id, prompt, directory=ws)

            preview, final_msg = self._await_finished(oc_session_id, directory=ws)
            self._persist_conversation(sid, prompt, final_msg)
            self._mark_done(sid, batch_id, last_preview=preview)
            self._notify_scan(session_row, final_msg, ok=True)
        except _SessionTimeout as e:
            self._mark_failed(sid, batch_id, error=f'timeout after {e.seconds}s')
            self._notify_scan(session_row, None, ok=False)
        except Exception as e:
            self._mark_failed(sid, batch_id,
                              error=f'{type(e).__name__}: {e}'[:500])
            self._notify_scan(session_row, None, ok=False)

    def _notify_scan(self, session_row, final_msg, ok: bool):
        if not session_row.get('scan_task_id'):
            return
        try:
            from utils.ai_scan_engine import on_child_finished
            on_child_finished(session_row, final_msg, ok=ok)
        except Exception:
            traceback.print_exc()

    def _fetch_batch_prompt(self, batch_id: str) -> str | None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT prompt FROM ai_chat_batches WHERE id = %s",
                    (batch_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def _set_opencode_id(self, session_id: str, oc_session_id: str):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET opencode_session_id = %s "
                    "WHERE id = %s",
                    (oc_session_id, session_id),
                )
            conn.commit()

    def _await_finished(self, oc_session_id: str,
                        directory: str = '') -> tuple[str | None, dict | None]:
        """Poll until the latest assistant message reports finished.

        Returns (preview_first_line, full_message_dict). The full message is
        what gets persisted to ai_chat_messages so the user can read the
        conversation via the 查看 button on the batch dashboard.
        """
        deadline = time.time() + self.SESSION_TIMEOUT_SEC
        last_preview = None
        last_message = None
        while time.time() < deadline:
            msgs = opencode_client.list_messages(oc_session_id,
                                                 directory=directory) or []
            for m in reversed(msgs):
                if m.get('role') == 'assistant':
                    last_preview = self._preview_from(m)
                    last_message = m
                    if m.get('finished'):
                        return last_preview, last_message
                    break
            time.sleep(self.POLL_INTERVAL_SEC)
        raise _SessionTimeout(self.SESSION_TIMEOUT_SEC)

    @staticmethod
    def _preview_from(message: dict) -> str | None:
        for part in (message.get('content') or []):
            if part.get('type') == 'text' and part.get('text'):
                t = part['text'].strip().splitlines()
                return (t[0] if t else '')[:200]
        return None

    def _persist_conversation(self, session_id: str,
                              prompt: str, assistant_msg: dict | None):
        """Write the user prompt + assistant final message into ai_chat_messages.

        Without this, the 查看 button on the batch dashboard opens an empty
        thread — the worker drove the conversation through OpenCode's SSE and
        never touched Flask's message store. Best-effort; never raises.
        """
        try:
            import uuid as _uuid
            import json as _json
            user_content = [{'type': 'text', 'text': prompt}]
            parts = (assistant_msg or {}).get('content') or []
            assistant_content = parts if parts else [{'type': 'text', 'text': ''}]
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s, %s, %s, %s::jsonb)",
                        (str(_uuid.uuid4()), session_id, 'user',
                         _json.dumps(user_content)),
                    )
                    cur.execute(
                        "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s, %s, %s, %s::jsonb)",
                        (str(_uuid.uuid4()), session_id, 'assistant',
                         _json.dumps(assistant_content)),
                    )
                conn.commit()
        except Exception:
            traceback.print_exc()  # best-effort; don't crash _run_one

    def _mark_done(self, session_id: str, batch_id: str,
                   last_preview: str | None):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions "
                    "SET status = 'completed', last_message_preview = %s "
                    "WHERE id = %s",
                    (last_preview, session_id),
                )
                cur.execute(
                    "UPDATE ai_chat_batches SET done = done + 1 WHERE id = %s",
                    (batch_id,),
                )
            conn.commit()
        _recompute_batch_status(batch_id)

    def _mark_failed(self, session_id: str, batch_id: str, error: str):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions "
                    "SET status = 'failed', error_message = %s "
                    "WHERE id = %s",
                    (error, session_id),
                )
                cur.execute(
                    "UPDATE ai_chat_batches SET failed = failed + 1 "
                    "WHERE id = %s",
                    (batch_id,),
                )
            conn.commit()
        _recompute_batch_status(batch_id)
