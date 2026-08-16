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
import logging
import os
import shutil
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from psycopg2.extras import RealDictCursor

from db import get_db
from utils.workspace import create_session_workspace, _rm_force
from utils.workspace_changes import git_changes, record_session_files
from utils.ai_message_meta import meta_from_info, public_meta

logger = logging.getLogger(__name__)


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
                     directory: str = '', agent: str = '', model: str = '') -> dict:
        """Fire the prompt asynchronously.  Returns a stub dict so callers can
        discard the return value — the real work happens on the SSE stream.

        `model` ("<providerID>/<modelID>") is the per-batch model; empty falls
        back to the global OPENCODE_MODEL (which itself may be empty, leaving the
        choice to OpenCode / the agent default).
        """
        from config import OPENCODE_MODEL
        self._client().send_prompt_async(
            oc_session_id, prompt,
            model=model or OPENCODE_MODEL,
            directory=directory,
            agent=agent,
        )
        return {'id': oc_session_id}

    # Finish reasons that mean "the model will produce another message" (the
    # agent is about to run a tool and continue). Anything else is terminal.
    _CONTINUATION_FINISH = {'tool-calls', 'tool_use'}

    def list_messages(self, oc_session_id: str,
                      directory: str = '') -> list:
        """Poll OpenCode's REST message list and map each assistant message to the
        shape the worker's _await_finished() expects:
            {'role': 'assistant', 'finished': True/False, 'content': [...]}

        Completion is derived from the message's `finish` reason, NOT from the
        one-shot `session.idle` event. A turn is finished once the latest
        assistant message has `time.completed` set AND a terminal `finish`
        (anything other than 'tool-calls'). This is deterministic: re-polling
        always re-reads the same state, so a turn that ends between two polls is
        never missed — unlike the old event-window approach which dropped
        `session.idle` if it fired in the gap (the source of children hanging in
        'running' until the 30-min timeout under concurrency).

        A transient REST error is reported as "not finished" so the poll loop
        retries; a persistent failure still hits SESSION_TIMEOUT_SEC -> failed.
        """
        import requests
        try:
            raw = self._client().get_messages(oc_session_id, directory=directory) or []
        except requests.RequestException:
            return [{'role': 'assistant', 'finished': False, 'content': []}]

        out: list = []
        for m in raw:
            info = m.get('info') or {}
            if info.get('role') != 'assistant':
                continue
            parts = m.get('parts') or m.get('content') or []
            content: list = []
            running_tool = False
            for p in parts:
                t = p.get('type')
                if t == 'text' and p.get('text'):
                    content.append({'type': 'text', 'text': p.get('text', '')})
                elif t == 'tool':
                    st = p.get('state') or {}
                    status = st.get('status')
                    out_val = st.get('output')
                    # Keep tool parts visible to progress tracking (a delegating
                    # subagent shows up as a long-running `task` tool — invisible
                    # if we only track text → false "stalled" kill).
                    content.append({'type': 'tool_use', 'name': p.get('tool'),
                                    'status': status,
                                    'output_len': len(out_val) if isinstance(out_val, str) else 0})
                    if status in (None, '', 'pending', 'running'):
                        running_tool = True
            finish = info.get('finish')
            completed = (info.get('time') or {}).get('completed')
            finished = bool(completed) and finish not in (None, '') \
                and finish not in self._CONTINUATION_FINISH
            # `error` 必须透出：出错的一轮往往没有 finish / time.completed / 文本，
            # 不带出来的话上层看到的就是「什么都没发生」，只能等 STALL 超时。
            out.append({'role': 'assistant', 'finished': finished, 'content': content,
                        'finish': finish, 'running_tool': running_tool,
                        'error': info.get('error')})
        return out or [{'role': 'assistant', 'finished': False, 'content': [],
                        'finish': None, 'running_tool': False, 'error': None}]

    def get_messages(self, oc_session_id: str, directory: str = '') -> list:
        """Raw OpenCode message list (each {'info':..., 'parts':[...]}). Used by
        _persist_conversation to store the FULL conversation incl. tool parts."""
        return self._client().get_messages(oc_session_id, directory=directory) or []

    def list_agents(self, directory: str = '') -> list:
        """OpenCode agents available in `directory`'s scope (global + project),
        each {'name','mode',...}. Used to validate a batch's chosen agent."""
        return self._client().list_agents(directory=directory) or []


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

    Raises FileNotFoundError if the staged input is gone (e.g. the staging dir
    was swept after its 24h TTL, or the batch row outlived its files). Silently
    producing an EMPTY uploads/ would be worse: the prompt still tells the agent
    to read uploads/<name> (see _with_input_hint), so the child would "succeed"
    with garbage output. Raising here makes _run_one mark the child failed.
    """
    ws = create_session_workspace(_workspace_root(), user_id, session_id)
    src = Path(_workspace_root()) / staged_file_path
    if not src.exists():
        raise FileNotFoundError(
            f'输入文件不存在或已被清理: {staged_file_path}')
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
            else:
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
    def __init__(self, seconds: int, reason: str = 'timeout'):
        super().__init__(f'{reason} after {seconds}s')
        self.seconds = seconds
        self.reason = reason


class _TurnFailed(Exception):
    """OpenCode 明确报告这一轮失败了（AssistantMessage.error）。

    与 _SessionTimeout 的区别是「知道原因」：OpenCode 的 OpenAPI 把 error 定义为
    ProviderAuthError / UnknownError / MessageOutputLengthError /
    MessageAbortedError / StructuredOutputError / ContextOverflowError / APIError
    七选一。这类消息通常没有文本片段、没有运行中的工具，而 `finish` 与
    `time.completed` 在规格里都不是必填、出错时一般也不带 —— 所以若不读 error，
    它在进度签名里就是「什么都没发生」，会一路拖到 STALL_TIMEOUT_SEC 才以
    `stalled (no progress)` 失败：把一个精确、可立即行动的原因，换成了一个通用且
    误导的超时（provider 密钥没配好时，整批子任务会全部呈现为这个症状）。
    """

    def __init__(self, error: dict):
        from utils.opencode_parts import format_opencode_error
        self.error = error or {}
        super().__init__(format_opencode_error(self.error))
        self.name = self.error.get('name') or 'UnknownError'


MAX_SUBTASK_DEPTH = 5


def discover_subtasks(messages: list, known: dict, parent_depth: int,
                      parent_sid: str | None) -> dict:
    """扫描一批（同一个会话拉到的）原始 OpenCode 消息，找出其中携带的 subtask
    part、且还不在 `known` 里的子代理，返回新发现的
    {sessionID: {'depth', 'parent_id', 'agent', 'description'}}。不递归深入
    已知子代理自己的消息——调用方对每个已知子代理的消息列表各自再调一次本
    函数，`parent_depth`/`parent_sid` 传该子代理自己的 depth/id（顶层扫描
    传 depth=0, parent_sid=None）。

    子代理的真实 sessionID 不在 SubtaskPart.sessionID（那是 PartBase 的标准
    "所属会话"字段，等于父/根会话自己），而在同一消息里配套产出的
    tool:'task' part 的 state.metadata.sessionId——按出现顺序与 subtask part
    一一配对。"""
    found = {}
    for m in (messages or []):
        parts = m.get('parts') or []
        task_tools = []
        subtask_parts = []
        for p in parts:
            if p.get('type') == 'tool' and p.get('tool') == 'task':
                sid = ((p.get('state') or {}).get('metadata') or {}).get('sessionId')
                if sid:
                    task_tools.append((sid, p))
            elif p.get('type') == 'subtask':
                subtask_parts.append(p)
        # subtask part 按出现顺序与 tool:'task' 一一配对（/command 路径两者并存）；
        # 配不上对的 tool:'task'（自然语言委托，只有 tool part）单独发现。
        paired = 0
        for i, sp in enumerate(subtask_parts):
            if i >= len(task_tools):
                break
            paired += 1
            sid, _tp = task_tools[i]
            if sid in known or sid in found:
                continue
            if parent_depth + 1 > MAX_SUBTASK_DEPTH:
                continue
            found[sid] = {'depth': parent_depth + 1, 'parent_id': parent_sid,
                          'agent': sp.get('agent'), 'description': sp.get('description'),
                          '_parent_session_id': sp.get('sessionID'),
                          '_part_id': sp.get('id')}
        for sid, tp in task_tools[paired:]:
            if sid in known or sid in found:
                continue
            if parent_depth + 1 > MAX_SUBTASK_DEPTH:
                continue
            inp = ((tp.get('state') or {}).get('input') or {})
            found[sid] = {'depth': parent_depth + 1, 'parent_id': parent_sid,
                          'agent': inp.get('subagent_type'),
                          'description': inp.get('description'),
                          '_parent_session_id': m.get('info', {}).get('sessionID'),
                          '_part_id': tp.get('id')}
    return found


def subtask_status_from_messages(messages: list) -> tuple[str, str | None]:
    """从子代理自己的原始消息列表判定当前状态：('failed', 原因) / ('completed', None)
    / ('running', None)。跟批任务顶层的完成/报错判定同源（复用
    opencode_parts.format_opencode_error），不是另起一套。"""
    from utils.opencode_parts import format_opencode_error
    for m in reversed(messages or []):
        info = m.get('info') or {}
        if info.get('role') != 'assistant':
            continue
        if info.get('error'):
            return 'failed', format_opencode_error(info['error'])
        finish = info.get('finish')
        completed = (info.get('time') or {}).get('completed')
        if completed and finish not in (None, '', 'tool-calls', 'tool_use'):
            return 'completed', None
        return 'running', None
    return 'running', None


class BatchWorker:
    MAX_CONCURRENT = 3
    POLL_INTERVAL_SEC = 2
    # Hard per-child cap. Default 0 = NO cap: a batch child runs as long as it
    # keeps making progress (legit long tasks / subagent delegations shouldn't be
    # killed by an arbitrary clock). Override with AI_BATCH_SESSION_TIMEOUT_SEC.
    SESSION_TIMEOUT_SEC = int(os.getenv('AI_BATCH_SESSION_TIMEOUT_SEC', '0'))
    # Persist the conversation at most this often off the REST poll, so a running
    # batch child can be viewed live without flooding the DB.
    PROGRESS_PERSIST_SEC = 2.5
    # The real safety net (kept even with no hard cap): if the turn produces NO
    # new output AND nothing is in flight for this long, it's a half-open/frozen
    # turn — fail it. Tool/subagent activity counts as progress (see
    # _progress_signature / the active_tool reset in _await_finished), so a
    # working turn never trips this; only a genuinely dead one does.
    STALL_TIMEOUT_SEC = 180

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
        logger.info('batch dispatcher started')
        try:
            while not self._stop.is_set():
                self._wake.wait(timeout=10)
                self._wake.clear()
                if self._stop.is_set():
                    break
                if not self._dispatch_tick():
                    # The tick hit an error (DB hiccup, pool exhaustion). Back off
                    # briefly to avoid hot-looping, but KEEP the loop alive.
                    self._stop.wait(self.POLL_INTERVAL_SEC)
        finally:
            # Normally only reached via stop(). If it's ever reached otherwise,
            # this log turns a silent dead worker — the cause of "批任务一直待运行"
            # — into something diagnosable.
            logger.info('batch dispatcher exited (stop=%s)', self._stop.is_set())

    def _dispatch_tick(self) -> bool:
        """Run one claim+submit cycle. Returns True normally, False if an
        exception was caught.

        NEVER raises: a transient failure here (a DB hiccup, connection-pool
        exhaustion) must not propagate out of _dispatcher_loop and kill the
        worker thread — that would leave every future batch hanging in 'pending'
        forever with no error and no recovery until Flask restarts."""
        try:
            with self._lock:
                free = self.MAX_CONCURRENT - len(self._running_session_ids)
            if free <= 0:
                return True
            pending = self._claim_pending_sessions(limit=free)
            for s in pending:
                with self._lock:
                    self._running_session_ids.add(s['id'])
                self._executor.submit(self._safe_run_one, s)
            return True
        except Exception:
            logger.exception('batch dispatcher tick failed; will retry')
            return False

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
                # Reflect "in progress" in the batch the moment a child starts —
                # otherwise the batch stays 'pending' (sidebar shows 待运行) until
                # the FIRST child reaches a terminal state, even while children run.
                batch_ids = list({r['batch_id'] for r in rows if r.get('batch_id')})
                if batch_ids:
                    cur.execute(
                        "UPDATE ai_chat_batches SET status = 'running' "
                        "WHERE id = ANY(%s) AND status = 'pending'",
                        (batch_ids,),
                    )
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

    @staticmethod
    def _with_input_hint(prompt: str, session_row) -> str:
        """Prepend a hint telling the agent where its uploaded input file is, so it
        reads it instead of asking for a path. Scan-task children already carry their
        own context preamble (ai_scan_engine.assemble_prompt), so they're left as-is;
        children without an input file are unchanged too."""
        if session_row.get('scan_task_id'):
            return prompt
        rel = session_row.get('batch_input_file') or ''
        name = os.path.basename(rel.replace('\\', '/'))
        if not name:
            return prompt
        return (f'本任务的输入文件已放在工作区 uploads/{name}，'
                f'请先读取该文件的内容，再完成下面的要求：\n\n{prompt}')

    def _run_one(self, session_row):
        sid = session_row['id']
        user_id = session_row['user_id']
        batch_id = session_row['batch_id']
        ctx = self._fetch_batch_context(batch_id)
        if ctx is None:
            # Batch was deleted between claim and prompt fetch.
            # FK CASCADE has already removed our session row; nothing to mark.
            # Scan-task children are intentionally NOT notified via _notify_scan on
            # this path: recovery is handled by the orphan sweep (running rows with
            # no live session get reset to pending).
            return
        prompt, agent, model, provision_repo, provision_ref = ctx

        # Detect "continue" mode: opencode_session_id already set + continue_prompt
        is_continue = bool(session_row.get('opencode_session_id')
                          and session_row.get('continue_prompt'))
        if is_continue:
            prompt = session_row['continue_prompt']
            # Clear continue_prompt immediately so it's not re-sent on retry
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE ai_chat_sessions SET continue_prompt = NULL "
                                "WHERE id = %s", (sid,))
                conn.commit()
        else:
            prompt = self._with_input_hint(prompt, session_row)

        ws = None
        try:
            if is_continue:
                # Reuse existing workspace — don't re-prepare
                ws = session_row.get('workspace_path')
                if not ws or not os.path.isdir(ws):
                    self._mark_failed(sid, batch_id,
                                      error='继续对话失败：工作区已不存在')
                    self._notify_scan(session_row, None, ok=False)
                    return
                oc_session_id = session_row['opencode_session_id']
            else:
                ws = _prepare_workspace(user_id, sid, session_row['batch_input_file'] or '')
                # Provision project-level agents/skills BEFORE the session starts —
                # OpenCode binds the agent at prompt time, so the repo must be in
                # .opencode/ first. Degrades gracefully: a clone failure doesn't fail
                # the child (global agents/skills still work); we just post a notice.
                prov_warn = self._provision_workspace(ws, provision_repo, provision_ref)
                if prov_warn:
                    self._persist_provision_notice(sid, prov_warn)
                # Inject global skills (symlink/copy from central storage)
                try:
                    from utils.global_skills import inject_global_skills
                    injected = inject_global_skills(ws)
                    if injected:
                        self._persist_provision_notice(
                            sid, f'已注入全局技能: {", ".join(injected)}')
                except Exception:
                    pass  # best-effort: don't fail the child
                # Fail FAST on an unusable agent. OpenCode silently produces nothing
                # for an unknown / subagent-as-primary agent, which would otherwise
                # hang until STALL_TIMEOUT (the "批任务一直待运行 with custom agent" bug).
                agent_err = self._check_agent(agent, ws)
                if agent_err:
                    self._mark_failed(sid, batch_id, error=agent_err)
                    self._notify_scan(session_row, None, ok=False)
                    return
                oc_session_id = opencode_client.create_session(directory=ws)
                self._set_opencode_id(sid, oc_session_id, ws)

            # Persist the prompt up front so opening this child mid-run shows the
            # question immediately.
            self._persist_user_prompt(sid, prompt)
            opencode_client.send_message(oc_session_id, prompt, directory=ws,
                                         agent=agent, model=model)

            # Persist the conversation progressively from the worker's own REST
            # polling (the path that already drives completion detection), so the
            # live view works without depending on OpenCode's SSE reaching a
            # background listener. Idempotent (keyed on OpenCode message ids).
            def _persist_progress():
                self._persist_conversation(sid, prompt, oc_session_id, None, directory=ws)
            preview, final_msg = self._await_finished(oc_session_id, directory=ws,
                                                      on_progress=_persist_progress)
            self._persist_conversation(sid, prompt, oc_session_id, final_msg, directory=ws)
            self._mark_done(sid, batch_id, last_preview=preview)
            self._notify_scan(session_row, final_msg, ok=True)
        except (_SessionTimeout, _TurnFailed) as e:
            # 两者都已自带可读原因，直接落库；不要加 `{type}: ` 前缀，那对用户是噪音。
            self._mark_failed(sid, batch_id, error=str(e)[:500])
            self._notify_scan(session_row, None, ok=False)
        except Exception as e:
            self._mark_failed(sid, batch_id,
                              error=f'{type(e).__name__}: {e}'[:500])
            self._notify_scan(session_row, None, ok=False)
        finally:
            # 收尾：无论成功失败，把工作区里已产生的新增/修改文件记进独立表
            # （best-effort，失败不影响子任务本身的状态落库）。
            if ws:
                self._record_workspace_files(sid, ws)

    def _record_workspace_files(self, session_id: str, ws: str):
        try:
            changes, _truncated, ok = git_changes(ws)
            if ok:
                record_session_files(session_id, changes)
        except Exception:
            pass  # best-effort

    def _persist_provision_notice(self, session_id: str, warning: str):
        """Insert a notice into the child's thread when workspace provisioning
        failed, so the user sees that it degraded to the global agents/skills.
        Inserted before the turn so it sorts to the top. Best-effort."""
        try:
            import uuid as _uuid
            import json as _json
            content = [{'type': 'text',
                        'text': f'⚠️ 预置仓库克隆失败，已使用全局 Agent / Skill 继续。\n\n{warning}'}]
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s, %s, 'assistant', %s::jsonb)",
                        (str(_uuid.uuid4()), session_id, _json.dumps(content)),
                    )
                conn.commit()
        except Exception:
            traceback.print_exc()

    def _notify_scan(self, session_row, final_msg, ok: bool):
        if not session_row.get('scan_task_id'):
            return
        try:
            from utils.ai_scan_engine import on_child_finished
            on_child_finished(session_row, final_msg, ok=ok)
        except Exception:
            traceback.print_exc()

    @staticmethod
    def _check_agent(agent, directory):
        """Return an error string if `agent` can't be used as the session's
        primary agent (unknown, or a subagent), else None. Empty agent (default)
        is always OK. If OpenCode can't be queried we don't block (return None) —
        create_session will surface a real connectivity error instead."""
        agent = (agent or '').strip()
        if not agent:
            return None
        try:
            agents = opencode_client.list_agents(directory=directory) or []
            names = {a.get('name') for a in agents}
            primary = {a.get('name') for a in agents if a.get('mode') == 'primary'}
        except Exception:
            return None
        if agent in primary:
            return None
        if agent in names:
            return (f'Agent「{agent}」是 subagent，不能作为批任务的主 Agent。'
                    f'请改用 primary Agent，或在 prompt 里用 @{agent} 调用它。')
        return (f'Agent「{agent}」不存在（OpenCode 未找到）。'
                f'请确认该 Agent 已在 OpenCode 安装，或通过「预置仓库」提供给子任务。')

    @staticmethod
    def _provision_workspace(ws: str, repo, ref):
        """Clone the batch's agent/skill repo into <ws>/.opencode/ so OpenCode
        discovers project-level agents/skills when the session's prompt is sent.

        The repo root is treated as the .opencode config dir (it should contain
        agent/, skill/, …). Shallow clone; the cloned .git is removed afterwards.

        Degrades gracefully: on failure returns a short warning string (the run
        continues with the global agents/skills) instead of raising — the caller
        surfaces the warning in the session. Returns None on success / no-op."""
        repo = (repo or '').strip()
        if not repo:
            return None
        dest = os.path.join(ws, '.opencode')
        args = ['git', 'clone', '--depth', '1']
        ref = (ref or '').strip()
        if ref:
            args += ['--branch', ref]
        args += [repo, dest]
        try:
            out = subprocess.run(args, capture_output=True, timeout=180)
            if out.returncode != 0:
                err = (out.stderr or b'').decode('utf-8', 'replace').strip()
                return f'预置仓库克隆失败 (rc={out.returncode}): {err[:300]}'
            gitdir = os.path.join(dest, '.git')
            if os.path.isdir(gitdir):
                shutil.rmtree(gitdir, onerror=_rm_force)  # git internals are read-only on Windows
            return None
        except Exception as e:
            return f'预置仓库克隆失败: {type(e).__name__}: {e}'

    def _fetch_batch_context(self, batch_id: str) -> tuple | None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT prompt, agent, model, provision_repo, provision_ref "
                    "FROM ai_chat_batches WHERE id = %s",
                    (batch_id,),
                )
                row = cur.fetchone()
                return (row[0], row[1] or '', row[2] or '', row[3] or '', row[4] or '') \
                    if row else None

    def _set_opencode_id(self, session_id: str, oc_session_id: str, ws: str = None):
        """Record the OpenCode session id and (so the SSE proxy can scope to it
        for live viewing) the workspace path on the child session row."""
        with get_db() as conn:
            with conn.cursor() as cur:
                if ws is not None:
                    cur.execute(
                        "UPDATE ai_chat_sessions SET opencode_session_id = %s, "
                        "workspace_path = %s WHERE id = %s",
                        (oc_session_id, ws, session_id),
                    )
                else:
                    cur.execute(
                        "UPDATE ai_chat_sessions SET opencode_session_id = %s "
                        "WHERE id = %s",
                        (oc_session_id, session_id),
                    )
            conn.commit()

    def _persist_user_prompt(self, session_id: str, prompt: str):
        """Persist the child's prompt as a user message (deterministic id, write
        once) so the thread shows the question while the turn runs."""
        import json as _json
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s, %s, 'user', %s::jsonb) ON CONFLICT (id) DO NOTHING",
                        (f'{session_id}:user', session_id,
                         _json.dumps([{'type': 'text', 'text': prompt}])),
                    )
                conn.commit()
        except Exception:
            traceback.print_exc()


    def _await_finished(self, oc_session_id: str,
                        directory: str = '',
                        on_progress=None) -> tuple[str | None, dict | None]:
        """Poll until the latest assistant message reports finished.

        Returns (preview_first_line, full_message_dict). The full message is
        what gets persisted to ai_chat_messages so the user can read the
        conversation via the 查看 button on the batch dashboard.

        `on_progress` (optional) is called at most every PROGRESS_PERSIST_SEC
        while the turn runs, so callers can persist the conversation live off the
        same REST poll. Best-effort: an error there never aborts the wait.
        """
        cap = self.SESSION_TIMEOUT_SEC
        deadline = (time.time() + cap) if cap and cap > 0 else None   # None = no hard cap
        last_preview = None
        last_message = None
        last_sig = None
        last_progress_at = time.time()
        last_persist_at = 0.0
        first = True
        while deadline is None or time.time() < deadline:
            msgs = opencode_client.list_messages(oc_session_id,
                                                 directory=directory) or []
            if on_progress and time.time() - last_persist_at >= self.PROGRESS_PERSIST_SEC:
                try:
                    on_progress()
                except Exception:
                    traceback.print_exc()
                last_persist_at = time.time()
            active_tool = False
            for m in reversed(msgs):
                if m.get('role') == 'assistant':
                    last_preview = self._preview_from(m)
                    last_message = m
                    # OpenCode 明说这一轮挂了 —— 立刻带着原因失败，不要等 STALL。
                    # 放在 finished 判断之前：出错的消息有时也会带上终态 finish，
                    # 那种情况下当成"成功"会更糟（子任务标 completed、输出为空）。
                    if m.get('error'):
                        raise _TurnFailed(m['error'])
                    if m.get('finished'):
                        return last_preview, last_message
                    # The model is mid tool-call (incl. delegating to a subagent
                    # via the `task` tool) — it's working, not stalled.
                    if m.get('running_tool') or m.get('finish') in ('tool-calls', 'tool_use'):
                        active_tool = True
                    break
            # No-progress watchdog: the signature changes whenever the turn emits
            # a new message, more text, or any tool activity. If it stays frozen
            # past STALL_TIMEOUT_SEC AND nothing is in flight, the turn is half-open
            # on OpenCode's side — fail rather than hang. A running tool/subagent is
            # NOT a stall (only the 30-min SESSION_TIMEOUT bounds those).
            sig = self._progress_signature(msgs)
            if first or sig != last_sig or active_tool:
                first = False
                last_sig = sig
                last_progress_at = time.time()
            elif time.time() - last_progress_at > self.STALL_TIMEOUT_SEC:
                raise _SessionTimeout(int(time.time() - last_progress_at),
                                      reason='stalled (no progress)')
            time.sleep(self.POLL_INTERVAL_SEC)
        raise _SessionTimeout(self.SESSION_TIMEOUT_SEC)

    @staticmethod
    def _progress_signature(msgs: list) -> tuple:
        """A cheap proxy for forward progress: (#assistant messages, total text
        length, tool activity). Changes whenever the model emits a new message,
        more text, OR a tool advances (new tool call / status change / growing
        output) — so a delegating subagent (a long-running `task` tool) counts as
        progress instead of looking 'stalled'."""
        count = 0
        total_text = 0
        tool_sig: list = []
        for m in msgs:
            if m.get('role') == 'assistant':
                count += 1
                for p in (m.get('content') or []):
                    if p.get('type') == 'text':
                        total_text += len(p.get('text') or '')
                    elif p.get('type') == 'tool_use':
                        tool_sig.append((p.get('name'), p.get('status'),
                                         p.get('output_len') or 0))
        return (count, total_text, tuple(tool_sig))

    @staticmethod
    def _content_from_parts(parts, subtask_status: dict | None = None,
                            subtask_id_map: dict | None = None) -> list:
        """Map one OpenCode message's parts to persisted typed content: text +
        tool_use + subtask_use (matches interactive build_content + the
        AiContentPart schema). Drops reasoning/step markers. 委托给
        utils.opencode_parts.map_part（chat_persist.py 共用同一份映射）；空文本
        的过滤保留在这里——这是原有实现的分工，map_part 本身不过滤。`subtask`
        part 自 Task 5 起放行给 map_part：批任务路径每次都重新拉取完整消息列表
        （REST 快照式，见 `_persist_conversation`/`_collect_subtasks`），调用方
        总能传入当前整棵子代理树算好的 `subtask_status`，所以占位气泡不会像
        Task 4/5 接入前那样卡死在默认的 'running'。`subtask_id_map` 把 subtask
        part 的 id 映射到正确的子会话 sessionID（修复 SubtaskPart.sessionID
        实际是父会话的 bug）。"""
        from utils.opencode_parts import map_part
        # /command 路径同一消息里 subtask part 与 tool:'task' part 并存且指向
        # 同一子会话：subtask part 的占位元数据（agent/description）更准确，
        # 先收集，tool part 派生的占位去重时让位。
        subtask_by_child = {}
        for p in (parts or []):
            if p.get('type') == 'subtask':
                mapped = map_part(p, subtask_status=subtask_status,
                                  subtask_id_map=subtask_id_map)
                if mapped:
                    subtask_by_child[mapped['subtaskId']] = mapped
        out = []
        seen_subtask_ids = set()
        for p in (parts or []):
            if p.get('type') not in ('text', 'tool', 'subtask', 'reasoning'):
                continue
            mapped = map_part(p, subtask_status=subtask_status,
                              subtask_id_map=subtask_id_map)
            if mapped is None:
                continue
            if mapped['type'] in ('text', 'reasoning') and not mapped['text'].strip():
                continue
            if mapped['type'] == 'subtask_use':
                mapped = subtask_by_child.get(mapped['subtaskId'], mapped)
                if mapped['subtaskId'] in seen_subtask_ids:
                    continue
                seen_subtask_ids.add(mapped['subtaskId'])
            out.append(mapped)
        return out

    @staticmethod
    def _preview_from(message: dict) -> str | None:
        for part in (message.get('content') or []):
            if part.get('type') == 'text' and part.get('text'):
                t = part['text'].strip().splitlines()
                return (t[0] if t else '')[:200]
        return None

    def _collect_subtasks(self, messages: list, known: dict, child_messages: dict,
                          parent_depth: int, parent_sid: str | None, directory: str):
        """阶段一（只发现、不持久化）：递归扫描 messages 里的子代理，拉取
        每一个的自己的消息、算出它自己的状态，全部记进 known/child_messages。
        `known` 在递归调用之间原地累积（同一个字典对象一路传下去），既防止
        重复处理同一个子代理，也是深度判断的依据。持久化留到阶段二统一做——
        必须先拿到整棵树的完整状态快照，各层的占位气泡才能在同一次持久化里
        全部用上最新状态，不会出现"父级先写了、子代理的状态后来才算出来"
        这种顺序问题。"""
        newly = discover_subtasks(messages, known, parent_depth, parent_sid)
        for sid, info in newly.items():
            known[sid] = info
            try:
                msgs = opencode_client.get_messages(sid, directory=directory) or []
            except Exception:
                msgs = []
            status, error = subtask_status_from_messages(msgs)
            info['status'] = status
            info['error'] = error
            child_messages[sid] = msgs
            self._collect_subtasks(msgs, known, child_messages, info['depth'], sid, directory)

    def _write_subtask(self, root_session_id: str, subtask_id: str, info: dict,
                       subtask_status: dict, child_messages: list,
                       subtask_id_map: dict | None = None):
        """阶段二：把一个子代理的摘要行 + 它当前拉到的全部消息 upsert 进库。
        `subtask_status` 是整棵树的完整状态快照（阶段一算好的），传给
        `_content_from_parts` 让这个子代理自己内容里（如果有）更深一层的
        占位气泡也能用上最新状态——跟顶层的处理方式统一。`subtask_id_map` 把
        subtask part 的 id 映射到正确的子会话 sessionID。"""
        import json as _json
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO ai_chat_subtasks "
                "  (id, root_session_id, parent_subtask_id, agent, description, status, "
                "   error_message, completed_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, "
                "        CASE WHEN %s IN ('completed','failed') THEN now() ELSE NULL END) "
                "ON CONFLICT (id) DO UPDATE SET "
                "  status = EXCLUDED.status, error_message = EXCLUDED.error_message, "
                "  completed_at = COALESCE(ai_chat_subtasks.completed_at, EXCLUDED.completed_at)",
                (subtask_id, root_session_id, info.get('parent_id'), info.get('agent'),
                 info.get('description'), info['status'], info.get('error'), info['status']),
            )
            for m in child_messages:
                minfo = m.get('info') or {}
                role = minfo.get('role')
                if role == 'user':
                    texts = [p.get('text', '') for p in (m.get('parts') or [])
                             if p.get('type') == 'text' and (p.get('text') or '').strip()]
                    if not texts:
                        continue
                    content = [{'type': 'text', 'text': t} for t in texts]
                    mid = minfo.get('id') or f'{subtask_id}:u:{id(m)}'
                    cur.execute(
                        "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                        "VALUES (%s, %s, 'user', %s) "
                        "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
                        (mid, subtask_id, _json.dumps(content)),
                    )
                    continue
                if role != 'assistant':
                    continue
                content = self._content_from_parts(m.get('parts'), subtask_status,
                                                   subtask_id_map)
                if not content:
                    continue
                mid = minfo.get('id') or f'{subtask_id}:a:{id(m)}'
                cur.execute(
                    "INSERT INTO ai_chat_subtask_messages (id, subtask_id, role, content) "
                    "VALUES (%s, %s, 'assistant', %s) "
                    "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
                    (mid, subtask_id, _json.dumps(content)),
                )
            conn.commit()

    def _persist_conversation(self, session_id: str, prompt: str,
                              oc_session_id: str, assistant_msg: dict | None,
                              directory: str = ''):
        """Persist the FULL conversation: the user prompt + every assistant
        message (mapped to text + tool_use parts) read from OpenCode's REST
        message list, so the batch child's thread shows tool bubbles like an
        interactive session. Falls back to `assistant_msg` if REST yields none.

        Idempotent — the user row uses a deterministic id and each assistant row
        is keyed on its OpenCode message id (ON CONFLICT DO UPDATE), so calling
        this repeatedly while a turn runs upserts the growing conversation
        instead of duplicating it (that's how the live view is driven).
        Best-effort; never raises."""
        try:
            import json as _json
            raw = []
            try:
                raw = opencode_client.get_messages(oc_session_id, directory=directory) or []
            except Exception:
                raw = []

            # 阶段一：递归发现整棵子代理树 + 各自的当前状态。
            known: dict = {}
            child_messages: dict = {}
            self._collect_subtasks(raw, known, child_messages, parent_depth=0,
                                   parent_sid=None, directory=directory)
            subtask_status = {sid: info['status'] for sid, info in known.items()}
            # subtask part 的 id -> 正确的子会话 sessionID（修复 SubtaskPart.sessionID
            # 实际是父会话的 bug）：发现阶段已经把 _part_id 记进了 info。
            subtask_id_map = {info['_part_id']: sid for sid, info in known.items()
                              if info.get('_part_id')}

            # 阶段二：先落每个子代理自己的行/消息，再落顶层——都用同一份
            # subtask_status / subtask_id_map，占位气泡不会有哪一层状态落后。
            for sid, info in known.items():
                self._write_subtask(session_id, sid, info, subtask_status,
                                    child_messages.get(sid, []), subtask_id_map)

            assistant_rows = []   # (message_id, content, meta)
            for m in raw:
                info = m.get('info') or {}
                if info.get('role') != 'assistant':
                    continue
                content = self._content_from_parts(m.get('parts'), subtask_status,
                                                   subtask_id_map)
                if content:
                    meta = public_meta(meta_from_info(info))
                    assistant_rows.append((info.get('id') or f'{session_id}:a:{len(assistant_rows)}',
                                           content, meta))
            if not assistant_rows:   # REST gave nothing usable — fall back to final msg
                parts = (assistant_msg or {}).get('content') or []
                assistant_rows.append((f'{session_id}:a:final',
                                       parts if parts else [{'type': 'text', 'text': ''}], None))
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                        "VALUES (%s, %s, 'user', %s::jsonb) ON CONFLICT (id) DO NOTHING",
                        (f'{session_id}:user', session_id,
                         _json.dumps([{'type': 'text', 'text': prompt}])),
                    )
                    for mid, content, meta in assistant_rows:
                        cur.execute(
                            "INSERT INTO ai_chat_messages (id, session_id, role, content, meta) "
                            "VALUES (%s, %s, 'assistant', %s::jsonb, %s::jsonb) "
                            "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, "
                            "  meta = COALESCE(EXCLUDED.meta, ai_chat_messages.meta)",
                            (mid, session_id, _json.dumps(content),
                             _json.dumps(meta) if meta else None),
                        )
                conn.commit()
        except Exception:
            traceback.print_exc()

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
