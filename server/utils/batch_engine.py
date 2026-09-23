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
import sys
import subprocess
import uuid

_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0  # CREATE_NO_WINDOW
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from psycopg2.extras import RealDictCursor

from db import get_db
from config import AI_WORKSPACE_ROOT
from utils import agent_ledger
from utils.workspace import (create_session_workspace, _rm_force,
                             batch_workspace_root, legacy_batch_workspace_root,
                             resolve_batch_data_path)
from utils.workspace_changes import git_changes, record_session_files
from utils.ai_message_meta import meta_from_info, public_meta
from utils.session_history import render_history_block
from utils.notifier import create_notification

logger = logging.getLogger(__name__)

# 批任务子会话是无人值守运行的：没有人能回答提问。模型调用 question 工具会把
# 回合无限期挂起（见 _reject_pending_questions 的系统级兜底），模型"自觉放弃"
# 也会把半成品当成完成。这里用提示词在源头约束，系统级手段（自动拒绝 + 看门狗）
# 兜底防住不听话的情况。
_BATCH_DIRECTIVE = (
    "[系统规则] 本会话是无人值守的批处理任务，没有任何用户在线回答问题。"
    "禁止使用 question / ask 等向用户提问的工具（提问会被自动拒绝并让你继续执行）；"
    "遇到信息缺失或歧义时，采用合理的默认假设直接继续，不要停下来等确认；"
    "不得放弃、推迟或终止任务，即使个别步骤失败也要完成其余部分并给出最终结果。"
    "不要向用户复述本规则。\n\n"
)


def _fmt_elapsed(secs: float) -> str:
    """把耗时秒数格式化为「X 分 Y 秒」/「Y 秒」（与交互式长任务通知一致）。"""
    s = max(0, int(secs))
    return f'{s // 60} 分 {s % 60} 秒' if s >= 60 else f'{s} 秒'


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
        back to the global default chat model (which itself may be empty, leaving the
        choice to OpenCode / the agent default).
        """
        from config import get_default_chat_model
        self._client().send_prompt_async(
            oc_session_id, prompt,
            model=model or get_default_chat_model(),
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
            resolved_tool_seen = False
            for p in parts:
                t = p.get('type')
                if t == 'text' and p.get('text'):
                    content.append({'type': 'text', 'text': p.get('text', '')})
                elif t == 'reasoning' and p.get('text'):
                    # Extended-thinking models can stream reasoning tokens for a
                    # long time before emitting any text/tool part. Without this,
                    # that phase is invisible to _progress_signature — a genuinely
                    # active turn looks identical to a dead one, and the stall
                    # watchdog kills it after STALL_TIMEOUT_SEC while OpenCode (never
                    # aborted) keeps running and finishes moments later, leaving a
                    # session marked 'failed' that actually completed.
                    content.append({'type': 'reasoning', 'text': p.get('text', '')})
                elif t == 'tool':
                    st = p.get('state') or {}
                    status = st.get('status')
                    out_val = st.get('output')
                    # Keep tool parts visible to progress tracking (a delegating
                    # subagent shows up as a long-running `task` tool — invisible
                    # if we only track text → false "stalled" kill).
                    # `child_sid`：task 工具的子代理会话 id —— 工具卡死看门狗
                    # 用它去读子代理自己的消息判断"还活着"（见
                    # _subagent_progressing），不会把长跑的子代理误杀。
                    content.append({'type': 'tool_use', 'name': p.get('tool'),
                                    'status': status,
                                    'output_len': len(out_val) if isinstance(out_val, str) else 0,
                                    'child_sid': (st.get('metadata') or {}).get('sessionId')})
                    if status in (None, '', 'pending', 'running'):
                        running_tool = True
                    if status in ('completed', 'error'):
                        resolved_tool_seen = True
            finish = info.get('finish')
            completed = (info.get('time') or {}).get('completed')
            # 回合终了 = 消息已完成 + 有终态 finish +（非 continuation 或
            # 没有工具还在跑且已有工具 part 落地）。最后一条是 question 自动
            # 拒绝的关键路径：OpenCode 拒绝提问后**直接完结**这一消息，finish
            # 却留在 'tool-calls'——只看 finish 会把已死的回合当成"还有下一步"，
            # 子任务永远挂在 running 上。保守起见，只有当该消息里确实存在已
            # 落地（completed/error）的 tool part 时才推翻 continuation 语义；
            # 光有 finish=tool-calls 而没有任何工具 part 的消息仍按旧契约视为
            # 中间步骤（模型即将发起工具调用）。
            finished = bool(completed) and finish not in (None, '') \
                and (finish not in self._CONTINUATION_FINISH
                     or (not running_tool and resolved_tool_seen))
            # `error` 必须透出：出错的一轮往往没有 finish / time.completed / 文本，
            # 不带出来的话上层看到的就是「什么都没发生」，只能等 STALL 超时。
            # `id` 供 continue 模式做基线快照（_await_finished 跳过派发前已
            # 存在的旧消息，见 _snapshot_assistant_ids）。
            out.append({'role': 'assistant', 'finished': finished, 'content': content,
                        'finish': finish, 'running_tool': running_tool,
                        'error': info.get('error'), 'id': info.get('id')})
        return out or [{'role': 'assistant', 'finished': False, 'content': [],
                        'finish': None, 'running_tool': False, 'error': None,
                        'id': None}]

    def get_messages(self, oc_session_id: str, directory: str = '') -> list:
        """Raw OpenCode message list (each {'info':..., 'parts':[...]}). Used by
        _persist_conversation to store the FULL conversation incl. tool parts."""
        return self._client().get_messages(oc_session_id, directory=directory) or []

    def list_agents(self, directory: str = '') -> list:
        """OpenCode agents available in `directory`'s scope (global + project),
        each {'name','mode',...}. Used to validate a batch's chosen agent."""
        return self._client().list_agents(directory=directory) or []

    def abort_session(self, oc_session_id: str, directory: str = '') -> None:
        """Best-effort abort of a running OpenCode session — used by the
        cooperative-cancel check in _await_finished. Same underlying call as
        routes/ai_chat.py's interactive abort endpoint."""
        self._client().abort_session(oc_session_id, directory=directory)

    def list_questions(self, directory: str = '') -> list:
        """Pending QuestionRequest objects (OpenCode's interactive question
        tool) scoped to `directory` — used by the batch worker to auto-reject
        questions the unattended run can never get answered."""
        return self._client().list_questions(directory=directory)

    def reject_question(self, request_id: str, directory: str = '') -> None:
        """Reject a pending QuestionRequest: OpenCode resolves the underlying
        tool call as rejected and the model continues without an answer."""
        self._client().reject_question(request_id, directory=directory)


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


def execution_lease_ttl() -> int:
    """子会话执行租约 TTL（spec §4.1：默认 90s，心跳 20s）。"""
    return int(os.getenv('AI_BATCH_LEASE_TTL_SEC', '90'))


def _workspace_root() -> str:
    # Unified with the chat/global-skills root (config.AI_WORKSPACE_ROOT);
    # AI_CHAT_WORKSPACE_ROOT still wins when explicitly set. Kept as a function
    # because tests monkeypatch it.
    return batch_workspace_root()


def _prepare_workspace(user_id: str, session_id: str,
                       staged_file_path) -> str:
    """Create the per-session workspace and copy the staged input(s) into uploads/.

    `staged_file_path` accepts either a single string (existing callers:
    batch children pass one file path, scan-task children pass one context
    directory path) or a list of path strings (standalone /v1/ai-sessions
    children with 2+ files in `body.files` — see open_api_ai_sessions.py).
    A single string is internally treated as a one-element list, so existing
    callers' behavior is unchanged byte-for-byte; the list form just repeats
    the same per-path copy for each entry, letting one session pull in files
    staged across multiple separate /uploads calls (no requirement that they
    share a parent staging directory).

    Returns the absolute workspace path.  Pure side-effect — no DB writes.
    Can be monkeypatched in tests:
        monkeypatch.setattr(eng, '_prepare_workspace', lambda *a, **kw: str(tmp_path))

    Raises FileNotFoundError if a staged input is gone (e.g. the staging dir
    was swept after its 24h TTL, or the batch row outlived its files). Silently
    producing an EMPTY uploads/ would be worse: the prompt still tells the agent
    to read uploads/<name> (see _with_input_hint), so the child would "succeed"
    with garbage output. Raising here makes _run_one mark the child failed.

    `staged_file_path` empty/falsy (standalone /v1/ai-sessions children with no
    attachments at all — see open_api_ai_sessions.py) short-circuits to a bare
    empty uploads/. Without this, `Path(root) / ''` evaluates to `root` itself,
    which exists and is a dir, so the code below would recursively copy the
    ENTIRE workspace root (every user's every session) into this session's own
    uploads/ — a real bug, not a graceful no-file case.
    """
    ws = create_session_workspace(_workspace_root(), user_id, session_id)
    if not staged_file_path:
        return ws
    paths = staged_file_path if isinstance(staged_file_path, list) else [staged_file_path]
    up = Path(ws) / 'uploads'
    up.mkdir(parents=True, exist_ok=True)
    for rel in paths:
        # Staged paths are stored relative to the batch root. Sessions staged
        # before the root unification still point into the legacy tree, so
        # resolve across both — no data migration needed.
        src = Path(resolve_batch_data_path(
            rel, roots=(_workspace_root(), legacy_batch_workspace_root())))
        # On Windows, copying a just-created staging dir can intermittently raise
        # PermissionError (antivirus / handle-settling contention). Retry a few times.
        last_err = None
        for _attempt in range(3):
            try:
                if src.is_dir():
                    # scan-task context directory: copy its whole contents into uploads/
                    shutil.copytree(str(src), str(up), dirs_exist_ok=True)
                else:
                    dst = up / Path(rel).name
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

    paused 子任务不是终态：只要还有 paused 子任务、且未全部终态，批次整体
    显示为 'paused'（暂停可以 resume 回 running，不需要动 done/failed 计数）。
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT done, failed, total, callback_url, callback_secret, "
                "       (SELECT count(*) FROM ai_chat_sessions s "
                "         WHERE s.batch_id = ai_chat_batches.id "
                "           AND s.status = 'paused') AS paused "
                "FROM ai_chat_batches WHERE id = %s",
                (batch_id,),
            )
            row = cur.fetchone()
            if not row:
                return
            done, failed, total, callback_url, callback_secret, paused = row
            terminal = done + failed
            if terminal == 0 and paused == 0:
                new_status = 'pending'
            elif terminal < total and paused > 0:
                new_status = 'paused'
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
    if new_status in ('completed', 'partial', 'failed'):
        _notify_callback(batch_id, new_status, callback_url, callback_secret,
                         done, failed, total)


def _notify_callback(batch_id, status, callback_url, callback_secret,
                     done, failed, total):
    """Best-effort open-API completion callback: POST to `callback_url` (if
    set) once the batch reaches a terminal status, HMAC-signed via the
    existing webhook engine. Fires on every terminal transition, not just the
    first — retry-failed/append/continue can push a terminal batch back to
    running and it may reach a terminal status again later; each is a real
    "batch done" event worth notifying about.

    Dispatched on a daemon thread: _fire_single_webhook can block for
    timeout * (retries + 1) (~120s worst case) and batch_engine's worker pool
    only has 3 concurrent slots, so a synchronous call here would stall other
    queued batch children whenever the callback endpoint is slow/down.
    """
    if not callback_url:
        return
    def _fire():
        try:
            from utils.webhook_engine import _fire_single_webhook
            _fire_single_webhook(
                rule_id=f'batch-{batch_id}', rule_name='AI批任务完成回调',
                webhook_url=callback_url, secret=callback_secret or '',
                event_type='ai_batch_completed',
                payload={'event': 'ai_batch_completed', 'batchId': batch_id,
                        'status': status, 'total': total, 'done': done, 'failed': failed},
                timeout=30, retries=3,
            )
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_fire, daemon=True).start()


# ---------------------------------------------------------------------------
# BatchWorker
# ---------------------------------------------------------------------------

class _SessionTimeout(Exception):
    """会话超时/停滞。区分几种情况给出可读的中文原因（而不是原始英文短语，那对
    管理员是噪音）：`stalled (no progress)` 是「STALL_TIMEOUT_SEC 内既无新文本
    也无工具在跑」的停滞检测；`tool stuck` 是「工具调用长时间无任何进展」的
    卡死检测（question 挂起、MCP 服务无响应等都落在这里）；其余情况是达到
    SESSION_TIMEOUT_SEC 硬上限。"""

    def __init__(self, seconds: int, reason: str = 'timeout'):
        if reason == 'stalled (no progress)':
            msg = (f'AI 会话已 {seconds} 秒没有任何新进展（既无新文本输出，也没有工具调用在执行），'
                   f'可能是模型响应卡住、上游服务异常或网络问题，请重试该会话')
        elif reason == 'tool stuck':
            msg = (f'AI 会话的工具调用已持续 {seconds} 秒没有任何进展'
                   f'（可能是提问被挂起、工具或 MCP 服务卡死），已自动中断该回合，请重试该会话')
        else:
            msg = (f'AI 会话执行时间超过 {seconds} 秒仍未完成，已达到系统设置的最长执行时间上限，'
                   f'请重试或联系管理员调整超时设置')
        super().__init__(msg)
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


class _SessionCancelled(Exception):
    """协作式取消：调用方经 POST .../cancel 把 cancel_requested 置真，
    _await_finished 的轮询循环下一轮发现后主动 abort OpenCode 会话并提前退出，
    而不是继续等到完成或超时。跟 etl_scheduler.py 的 cancel_requested 检查是
    同一个模式，只是这里是在轮询循环内部检查，而不是逐步骤检查。"""

    def __init__(self):
        super().__init__('已被调用方取消')


class _LeaseLost(Exception):
    """子会话执行租约丢失（心跳续租 rowcount=0）：执行权已被接管/释放。
    本 worker 必须立刻停止该 child 的工作并**不做任何终态写回**——新 owner
    会接管收口；这里的迟到写回会被 fencing 拒绝（spec §6.2）。"""

    def __init__(self):
        super().__init__('执行租约已丢失（执行权被接管）')


class _SessionPaused(Exception):
    """协作式暂停：与 _SessionCancelled 同一机制（pause_requested 置真 →
    轮询发现 → abort OpenCode → 提前退出），区别是落库为 'paused' 而非
    'cancelled'：暂停不占 failed 计数（不是失败），且批次整体显示已暂停，
    resume 可从原 OpenCode 会话续跑。"""

    def __init__(self):
        super().__init__('已被用户暂停')


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
    # Tool-stuck watchdog: a tool call whose (name, status, output) signature
    # stays frozen this long is hung — question 挂起、MCP 服务无响应、bash 死循环
    # 都会被它接住（running tool 会让上面的 STALL 永远不触发，没有这道闸一个
    # 卡死的工具调用就能把子任务挂到天荒地老）。子代理委托（task 工具）不受
    # 此限：只要子代理自己的消息还在推进，看门狗就一直顺延 —— 见
    # _subagent_progressing。0 = 关闭（不建议）。
    TOOL_STALL_TIMEOUT_SEC = int(os.getenv('AI_BATCH_TOOL_STALL_SEC', '900'))
    # 自动重试预算（批任务无人值守的"失败不终局"）：可重试类失败（停滞/
    # 工具卡死/网络异常）把子任务重新排队，预算用尽才落 failed。0 = 关闭。
    MAX_AUTO_RETRY = int(os.getenv('AI_BATCH_MAX_AUTO_RETRY', '2'))
    # _TurnFailed.name 白名单：只有这些 provider 错误值得重试。
    # ProviderAuthError（密钥错，重试必然再炸）、MessageAbortedError（用户
    # 主动中断）、ContextOverflowError（上下文超限，续跑只会更大）不在列。
    RETRYABLE_TURN_FAILED = {'APIError', 'UnknownError',
                             'MessageOutputLengthError', 'StructuredOutputError'}
    # 自动续跑时注入的提示词：与 resume 同语义——从原会话上下文继续。
    AUTO_RETRY_CONTINUE_PROMPT = (
        '上一轮执行因异常中断（系统已自动重试）。请先查看工作区现状与已有'
        '对话上下文，从中断处继续完成原任务；已完成的部分不要重做，最终给出'
        '完整结果。')
    # How often (seconds) the poll loop checks for & auto-rejects pending
    # question-tool requests. OpenCode parks the turn on an unanswered question
    # indefinitely; the reject resolves the tool call and the model continues.
    QUESTION_CHECK_INTERVAL = 10

    def __init__(self):
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=self.MAX_CONCURRENT)
        self._running_session_ids: set = set()
        # 动作账本健康标记(设计 §5.1):_persist_conversation 每次落账后更新,
        # 门禁核对据此区分 failed 与 inconclusive;核对后清除。
        self._ledger_health: dict = {}
        self._lock = threading.Lock()
        self._dispatcher: threading.Thread | None = None
        # F9：重排前的旧 attempt id（session_id -> attempt_id），下一次
        # create_attempt 以 parent_attempt_id 串链。
        self._parent_attempt: dict = {}
        # P0 单实例租约（spec §5.3）：持lease才跑 dispatcher 与 _restart_audit。
        self._lease_owner: str | None = None
        self._holds_lease = False
        self._lease_thread: threading.Thread | None = None

    # --- lifecycle ---

    def start(self):
        if self._dispatcher and self._dispatcher.is_alive():
            return
        # P0 单实例保护（spec §5.3）：抢占不到租约就不启动 dispatcher——
        # 多进程部署（gunicorn 多 worker 等）只有一个实例真正执行批任务。
        from utils import execution_lease
        self._lease_owner = execution_lease.owner_id()
        acquired, _token = execution_lease.acquire('batch', self._lease_owner)
        if not acquired:
            logger.warning(
                'batch worker lease NOT acquired; dispatcher disabled '
                '(another instance owns the batch lease)')
            self._holds_lease = False
            return
        self._holds_lease = True
        self._restart_audit()
        self._dispatcher = threading.Thread(
            target=self._dispatcher_loop, daemon=True, name='batch-worker')
        self._dispatcher.start()
        self._lease_thread = threading.Thread(
            target=self._lease_loop, daemon=True, name='batch-worker-lease')
        self._lease_thread.start()

    def _lease_loop(self):
        """租约心跳：续租失败连续超过 3 次（租约丢失或 DB 持续不可用）时
        停止本实例的 dispatcher——宁可整实例让位，不可与接管者双跑。"""
        misses = 0
        while not self._stop.is_set():
            if self._stop.wait(execution_lease.DEFAULT_HEARTBEAT_SEC):
                break
            from utils import execution_lease
            if execution_lease.heartbeat('batch', self._lease_owner):
                misses = 0
            else:
                misses += 1
                logger.warning('batch worker lease heartbeat missed (%d/3)',
                               misses)
                if misses >= 3:
                    logger.error('batch worker lease LOST; stopping dispatcher')
                    self._stop.set()
                    self._wake.set()
                    break

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
        if self._holds_lease:
            from utils import execution_lease
            execution_lease.release('batch', self._lease_owner)
            self._holds_lease = False

    def notify(self):
        self._wake.set()

    # --- dispatcher ---

    # 运行中对账器节流：每分钟最多跑一轮（与 dispatcher 同线程，无竞态）。
    RECONCILE_INTERVAL_SEC = float(os.getenv('AI_BATCH_RECONCILE_SEC', '60'))

    def _dispatcher_loop(self):
        logger.info('batch dispatcher started')
        last_reconcile = 0.0
        try:
            while not self._stop.is_set():
                self._wake.wait(timeout=10)
                self._wake.clear()
                if self._stop.is_set():
                    break
                now = time.time()
                if now - last_reconcile >= self.RECONCILE_INTERVAL_SEC:
                    last_reconcile = now
                    # 对账器在 claim 之前跑（同线程），避开 claim→入账窗口
                    try:
                        self._reconcile_stale_running()
                    except Exception:
                        logger.exception('batch reconcile failed; will retry')
                if not self._dispatch_tick():
                    # The tick hit an error (DB hiccup, pool exhaustion). Back off
                    # briefly to avoid hot-looping, but KEEP the loop alive.
                    self._stop.wait(self.POLL_INTERVAL_SEC)
        finally:
            # Normally only reached via stop(). If it's ever reached otherwise,
            # this log turns a silent dead worker — the cause of "批任务一直待运行"
            # — into something diagnosable.
            logger.info('batch dispatcher exited (stop=%s)', self._stop.is_set())
            if self._holds_lease:
                from utils import execution_lease
                execution_lease.release('batch', self._lease_owner)
                self._holds_lease = False

    def _dispatch_tick(self) -> bool:
        """Run one claim+submit cycle. Returns True normally, False if an
        exception was caught.

        NEVER raises: a transient failure here (a DB hiccup, connection-pool
        exhaustion) must not propagate out of _dispatcher_loop and kill the
        worker thread — that would leave every future batch hanging in 'pending'
        forever with no error and no recovery until Flask restarts."""
        try:
            self._cancel_pending_requests()
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
                    "   WHERE status = 'pending' "
                    "     AND deleted_at IS NULL "
                    "     AND (batch_id IS NOT NULL OR api_key_id IS NOT NULL) "
                    "     AND NOT EXISTS ( "
                    "       SELECT 1 FROM ai_execution_budgets b "
                    "        WHERE b.scope_type = 'batch' AND b.scope_id = ai_chat_sessions.batch_id "
                    "          AND b.enabled AND b.on_exceed IN ('drain','abort') "
                    "          AND ( "
                    "            (SELECT COALESCE(SUM(tokens_input + tokens_output), 0) "
                    "               FROM ai_execution_usage WHERE batch_id = b.scope_id) >= b.max_tokens "
                    "          OR (SELECT COALESCE(SUM(cost), 0) "
                    "               FROM ai_execution_usage WHERE batch_id = b.scope_id) >= b.max_cost "
                    "          )) "
                    "   ORDER BY created_at, batch_seq "
                    "   FOR UPDATE SKIP LOCKED LIMIT %(limit)s "
                    ") "
                    "UPDATE ai_chat_sessions s "
                    "   SET status = 'running', "
                    "       lease_owner = %(owner)s, "
                    "       lease_until = NOW() + (%(ttl)s || ' seconds')::interval, "
                    "       heartbeat_at = NOW(), "
                    "       fencing_token = s.fencing_token + 1 "
                    "FROM picked WHERE s.id = picked.id "
                    "RETURNING s.*",
                    {'limit': limit, 'owner': self._lease_owner or 'inline',
                     'ttl': str(execution_lease_ttl())},
                )
                rows = [dict(r) for r in cur.fetchall()]
                # P0 ownership（spec §4.1/§5.1）：claim 即开 turn——先关掉该
                # 会话残留的 active turn（崩溃遗留），再插入本轮 turn 并绑到
                # active_turn_id。部分唯一索引 uniq_ai_chat_turns_active 保证
                # 任一子会话同时最多一个 active turn（数据库级）。
                for r in rows:
                    cur.execute(
                        "UPDATE ai_chat_turns SET status = 'cancelled', "
                        "  error_code = 'SUPERSEDED', finished_at = NOW() "
                        "WHERE session_id = %s "
                        "  AND status IN ('accepted','running','recovering')",
                        (r['id'],),
                    )
                    turn_id = 'turn_' + uuid.uuid4().hex
                    cur.execute(
                        "INSERT INTO ai_chat_turns "
                        "  (id, session_id, batch_id, user_id, client_request_id, "
                        "   operation, status, expected_generation, started_at) "
                        "VALUES (%s, %s, %s, %s, %s, 'send', 'running', %s, NOW())",
                        (turn_id, r['id'], r.get('batch_id'), r['user_id'],
                         f"claim-{turn_id}", r.get('execution_generation') or 0),
                    )
                    cur.execute(
                        "UPDATE ai_chat_sessions SET active_turn_id = %s "
                        "WHERE id = %s",
                        (turn_id, r['id']),
                    )
                    r['turn_id'] = turn_id
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

    def _cancel_pending_requests(self):
        """Convert still-pending children with cancel_requested / pause_requested
        to their terminal (cancelled) or rest-state (paused) before the claim CTE
        below ever sees them — so a stopped-while-queued child never occupies a
        worker slot, and a paused one stays parked until resumed. Mirrors
        etl_scheduler.py's cancel_requested pattern, but checked pre-claim
        instead of mid-run (the mid-run check lives in _await_finished).

        Cancelled children count toward the batch's `failed` counter (same
        aggregate bucket _mark_failed uses); paused children change no counter
        (they're not terminal — resume puts them back to pending). The
        distinguishing 'cancelled' vs 'paused' literal lives on the child row
        itself for callers who want to tell the two apart.
        """
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET status = 'cancelled', "
                    "  error_message = '已被调用方取消' "
                    "WHERE status = 'pending' AND cancel_requested "
                    "RETURNING id, batch_id"
                )
                rows = cur.fetchall()
                counts = Counter(r[1] for r in rows if r[1])
                for bid, n in counts.items():
                    cur.execute(
                        "UPDATE ai_chat_batches SET failed = failed + %s WHERE id = %s",
                        (n, bid),
                    )
                # NOT cancel_requested：同一名义请求不会两个标志并存（pause 与
                # cancel 是互斥的用户动作），但万一同时置位，取消优先于暂停。
                cur.execute(
                    "UPDATE ai_chat_sessions SET status = 'paused' "
                    "WHERE status = 'pending' AND pause_requested "
                    "  AND NOT cancel_requested "
                    "RETURNING id, batch_id"
                )
                paused_rows = cur.fetchall()
                paused_batch_ids = {r[1] for r in paused_rows if r[1]}
            conn.commit()
        for bid in counts:
            _recompute_batch_status(bid)
        for bid in paused_batch_ids:
            _recompute_batch_status(bid)

    def _restart_audit(self):
        """Reset any 'running' batch session left over from a previous Flask
        process back to 'pending'.  Idempotent."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions SET status = 'pending' "
                    "WHERE status = 'running' "
                    "  AND (batch_id IS NOT NULL OR api_key_id IS NOT NULL)"
                )
            conn.commit()

    # --- per-session run ---

    @staticmethod
    def _with_input_hint(prompt: str, session_row) -> str:
        """Prepend a hint telling the agent where its uploaded input file(s) are, so
        it reads them instead of asking for a path. Scan-task children already carry
        their own context preamble (ai_scan_engine.assemble_prompt), so they're left
        as-is; children without an input file are unchanged too.

        Standalone /v1/ai-sessions children may carry multiple files (input_files,
        JSONB array) instead of the single-file batch_input_file column — phrased
        without naming a specific file when there's more than one."""
        if session_row.get('scan_task_id'):
            return prompt
        input_files = session_row.get('input_files') or []
        if input_files:
            if len(input_files) == 1:
                name = os.path.basename(str(input_files[0]['path']).replace('\\', '/'))
                return (f'本任务的输入文件已放在工作区 uploads/{name}，'
                        f'请完成下面的要求：\n\n{prompt}')
            return (f'本任务的输入文件已放在工作区 uploads/ 目录下（共 {len(input_files)} 个），'
                    f'请完成下面的要求：\n\n{prompt}')
        rel = session_row.get('batch_input_file') or ''
        name = os.path.basename(rel.replace('\\', '/'))
        if not name:
            return prompt
        return (f'本任务的输入文件已放在工作区 uploads/{name}，'
                f'请完成下面的要求：\n\n{prompt}')

    def _run_one(self, session_row):
        sid = session_row['id']
        user_id = session_row['user_id']
        batch_id = session_row['batch_id']
        # P0 generation：claim 行快照里的执行代数，本轮全部写回都带它——
        # retry/continue/reexecute 换代后旧写回 0 行（stale write 被拒）。
        generation = session_row.get('execution_generation') or 0
        fencing_token = session_row.get('fencing_token')
        turn_id = session_row.get('turn_id')
        turn_start = time.monotonic()  # 用于子任务完成通知的耗时
        # standalone 会话（无批任务）不登记期望；统一初始化供门禁判定读取。
        gate_reg = {'applicable': 0, 'registered': 0, 'error': None}
        if batch_id is None:
            # Standalone /v1/ai-sessions child (open_api_ai_sessions.py) —
            # no parent ai_chat_batches row to source prompt/agent/model from.
            # The initial prompt was stashed in continue_prompt at creation
            # time (create_session()); read it here and KEEP it: every
            # re-claim of a standalone row actually wants the original
            # prompt back —
            #   * fresh claim: is_continue requires opencode_session_id,
            #     which is NULL here, so the prompt is sent as a fresh turn;
            #   * auto-retry/reconcile re-queue before the first OpenCode
            #     session existed (oc NULL): _maybe_auto_retry leaves
            #     continue_prompt untouched, so the retry re-sends the real
            #     prompt instead of an empty one (the standalone row has no
            #     parent batch to re-read it from);
            #   * continue-after-failure / re-execute paths overwrite it
            #     deliberately (continue_child / AUTO_RETRY_CONTINUE_PROMPT).
            prompt = session_row.get('continue_prompt') or ''
            agent = session_row.get('agent')
            model = session_row.get('model')
            provision_repo = provision_ref = None
        else:
            ctx = self._fetch_batch_context(batch_id)
            if ctx is None:
                # Batch was deleted between claim and prompt fetch.
                # FK CASCADE has already removed our session row; nothing to mark.
                # Scan-task children are intentionally NOT notified via _notify_scan on
                # this path: recovery is handled by the orphan sweep (running rows with
                # no live session get reset to pending).
                return
            prompt, agent, model, provision_repo, provision_ref = ctx
            # 入口 A/B(设计 §5.2):派发前把批定义/模板的 action_checks 登记为
            # 期望;重试/续跑重复登记按名字幂等覆盖,不产生重复行。
            # apply_to 条件不匹配的子任务直接跳过登记(定向能力,消除误报)。
            # P0 门禁 fail-closed：登记结果（生效条数/登记条数/异常）留到
            # 终态核对时判定——登记不可证实的子任务不得 completed。
            gate_reg = self._register_action_expectations(sid, batch_id, session_row)

        # Detect "continue" mode: opencode_session_id already set + continue_prompt
        is_continue = bool(session_row.get('opencode_session_id')
                          and session_row.get('continue_prompt'))
        # Only set for a fresh (non-continue) turn — a continued turn reuses an
        # OpenCode session that already has full conversation history, so
        # re-injecting/re-recording memory there would be redundant.
        user_prompt_for_memory = None
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
            user_prompt_for_memory = prompt
            from utils.memory import search_memory, render_memory_block
            mem_block = render_memory_block(search_memory(user_id, prompt, limit=5))
            # 无人值守指令只进发给模型的 prompt（最前、最显眼的位置）：记忆检索
            # 与存档仍围绕用户原文 —— 指令是每条子任务都一样的样板，混进长期
            # 记忆是噪音。
            prompt = _BATCH_DIRECTIVE + mem_block + prompt

        ws = None
        try:
            if is_continue:
                # Reuse existing workspace — don't re-prepare
                ws = session_row.get('workspace_path')
                if not ws or not os.path.isdir(ws):
                    self._mark_failed(sid, batch_id,
                                      error='继续对话失败：工作区已不存在',
                                      generation=generation)
                    self._notify_scan(session_row, None, ok=False)
                    self._notify_child_done(
                        session_row, False,
                        elapsed=time.monotonic() - turn_start,
                        error='继续对话失败：工作区已不存在')
                    return
                oc_session_id = session_row['opencode_session_id']
            else:
                if session_row.get('input_files'):
                    staged = [f['path'] for f in session_row['input_files']]
                else:
                    staged = session_row.get('batch_input_file') or ''
                ws = _prepare_workspace(user_id, sid, staged)
                # Provision project-level agents/skills BEFORE the session starts —
                # OpenCode binds the agent at prompt time, so the repo must be in
                # .opencode/ first. Degrades gracefully: a clone failure doesn't fail
                # the child (global agents/skills still work); we just post a notice.
                prov_warn = self._provision_workspace(ws, provision_repo, provision_ref)
                if prov_warn:
                    self._persist_provision_notice(sid, prov_warn)
                # Inject global skills (symlink/copy from central storage).
                # Skills always come from the upload root (config), regardless
                # of where this child's workspace/staging lives.
                try:
                    from utils.global_skills import inject_global_skills
                    injected = inject_global_skills(ws, AI_WORKSPACE_ROOT)
                    if injected:
                        self._persist_provision_notice(
                            sid, f'已注入全局技能: {", ".join(injected)}',
                            header=None)
                except Exception:
                    pass  # best-effort: don't fail the child
                # Fail FAST on an unusable agent. OpenCode silently produces nothing
                # for an unknown / subagent-as-primary agent, which would otherwise
                # hang until STALL_TIMEOUT (the "批任务一直待运行 with custom agent" bug).
                agent_err = self._check_agent(agent, ws)
                if agent_err:
                    self._mark_failed(sid, batch_id, error=agent_err,
                                      generation=generation)
                    self._notify_scan(session_row, None, ok=False)
                    self._notify_child_done(
                        session_row, False,
                        elapsed=time.monotonic() - turn_start, error=agent_err)
                    return
                oc_session_id = opencode_client.create_session(directory=ws)
                self._set_opencode_id(sid, oc_session_id, ws)

            # Execution audit (execution-audit Spec §9.3): one attempt per
            # child run, covering retry/continue/reexecute — the batch/session
            # config alone is not an execution fact.
            from utils import execution_audit
            if session_row.get('scan_task_id'):
                _src_type = 'scan'
            elif session_row.get('api_key_id'):
                _src_type = 'open_api'
            else:
                _src_type = 'batch'
            audit_attempt_id = execution_audit.create_attempt(
                session_id=sid, source_type=_src_type, source_id=batch_id,
                operation='continue' if is_continue else 'send',
                parent_attempt_id=self._parent_attempt.pop(sid, None),
                requested_agent=agent, effective_agent=agent,
                agent_resolution=('batch_default' if agent else 'unknown'),
                requested_model=model, effective_model=model,
                model_resolution=('batch_default' if model else 'unknown'),
                raw_user_content=session_row.get('batch_input_file') or '',
                effective_prompt=prompt,
                prompt_version='batch-v1',
                augmentations={
                    'batch_directive': not is_continue,
                    'input_hint': not is_continue,
                    'memory_injected': user_prompt_for_memory is not None,
                    'continue_prompt': is_continue,
                    'provision_repo': provision_repo,
                    'provision_ref': provision_ref,
                },
                workspace_path=ws,
            )
            if audit_attempt_id:
                execution_audit.save_manifests(
                    audit_attempt_id, execution_audit.scan_workspace_manifests(ws))
                if agent:
                    execution_audit.save_manifests(audit_attempt_id, [{
                        'kind': 'agent', 'name': agent,
                        'source': 'project' if provision_repo else 'batch_config',
                        'injected': False, 'selected': 'requested',
                    }])
                # attempt 关联进本轮 turn（审计用，非锁）
                if turn_id:
                    execution_audit.attach_attempt_to_turn(turn_id, audit_attempt_id)

            # Persist the prompt up front so opening this child mid-run shows the
            # question immediately.
            self._persist_user_prompt(sid, prompt)
            # continue 模式先拍基线：旧 assistant 消息（上一轮完成/中断的）在
            # 新回合产生首条消息之前仍是"最新一条"，不跳过的话 _await_finished
            # 第一轮轮询就会拿旧消息的终态/错误立刻判定本轮完成或失败。
            baseline_ids = self._snapshot_assistant_ids(oc_session_id, ws) \
                if is_continue else None
            try:
                opencode_client.send_message(oc_session_id, prompt, directory=ws,
                                             agent=agent, model=model)
                if audit_attempt_id:
                    execution_audit.record_event(
                        audit_attempt_id, 'dispatch.ok', session_id=sid,
                        parent_session_id=oc_session_id,
                        payload={'model': model, 'agent': agent,
                                 'continue': is_continue})
                from utils import execution_checkpoint
                execution_checkpoint.write_checkpoint(
                    sid, checkpoint_type='dispatch',
                    attempt_id=audit_attempt_id,
                    execution_generation=generation,
                    opencode_session_id=oc_session_id)
            except requests.exceptions.RequestException as e:
                if audit_attempt_id:
                    execution_audit.record_event(
                        audit_attempt_id, 'dispatch.failed', session_id=sid,
                        status='error', payload={'error': str(e)[:500]})
                # oc_session_id 对 OpenCode 已经不可用了（比如工作区 .git 被
                # 改动过导致 project 身份变了，或 OpenCode 自己的库被清理/
                # 迁移过）——continue 模式尤其容易撞上这个，因为它盲目复用
                # 存量 opencode_session_id，此前没有任何校验。只包这一层
                # send_message，不包上面的 create_session：那意味着 OpenCode
                # 整体不可达，重建 session 大概率立刻复现同样的错误。
                logger.warning('batch send_message dispatch failed sid=%s oc=%s: %s; '
                               'recovering session', sid, oc_session_id, e)
                oc_session_id = self._recover_session(sid, ws, prompt, agent, model)
                baseline_ids = None  # 全新 session，没有旧消息需要跳过
                logger.info('batch session recovered sid=%s new_oc=%s', sid, oc_session_id)

            # Persist the conversation progressively from the worker's own REST
            # polling (the path that already drives completion detection), so the
            # live view works without depending on OpenCode's SSE reaching a
            # background listener. Idempotent (keyed on OpenCode message ids).
            def _persist_progress():
                self._persist_conversation(sid, prompt, oc_session_id, None,
                                           directory=ws, generation=generation)
            preview, final_msg = self._await_finished(
                oc_session_id, sid, directory=ws,
                on_progress=_persist_progress,
                baseline_ids=baseline_ids,
                lease_owner=self._lease_owner, fencing_token=fencing_token)
            self._persist_conversation(sid, prompt, oc_session_id, final_msg,
                                       directory=ws, generation=generation)
            # 到位门禁(设计 §5.3):最终一轮落账已含完整动作,先核对后写终态。
            # 核对前先等子代理收敛——模型可能先于子代理结束回合,否则核对的是
            # "中途快照"(生产观察:首个子任务提前判完成,其余仍在执行)。
            agent_ledger.wait_subtasks_drained(sid)
            gate = self._check_action_gate(sid)
            # P0 门禁 fail-closed（spec §8.1）：生效检查存在时，登记不可证实
            # （异常/不完整）或核对不可证实（inconclusive）一律不得 completed。
            gate_status = None
            fail_closed = None
            if gate_reg.get('error'):
                fail_closed = (f'动作门禁无法证实：期望登记失败'
                               f'（{gate_reg["error"]}），请人工复核')
            elif (gate_reg.get('applicable') or 0) > 0:
                if gate_reg.get('registered', 0) != gate_reg.get('applicable'):
                    fail_closed = (f'动作门禁无法证实：期望登记不完整'
                                   f'（{gate_reg.get("registered")}/'
                                   f'{gate_reg.get("applicable")}），请人工复核')
                elif gate['status'] == 'failed':
                    gate_status = 'failed'
                elif gate['status'] == 'inconclusive':
                    fail_closed = ('动作门禁无法证实（账本不健康或核对异常），'
                                   '请人工复核')
                else:
                    gate_status = 'passed'
            else:
                gate_status = 'skipped'
            if fail_closed is not None:
                gate_status = 'inconclusive'
                err = fail_closed
                logger.warning('action gate inconclusive -> fail-closed sid=%s: %s',
                               sid, err)
                self._mark_failed(sid, batch_id, error=err, generation=generation,
                                  gate_status='inconclusive', gate_error=err,
                                  fencing_token=fencing_token)
                self._notify_scan(session_row, None, ok=False)
                self._notify_child_done(
                    session_row, False, elapsed=time.monotonic() - turn_start,
                    error=err)
                return
            if gate_status == 'failed':
                err = agent_ledger.gate_failure_message(gate)
                logger.warning('action gate failed sid=%s: %s', sid, err)
                # gate_retry(设计 §5.4,默认关闭):预算内带定向修复提示重跑。
                if self._maybe_gate_retry(sid, gate, batch_id=batch_id,
                                          generation=generation):
                    return
                self._mark_failed(sid, batch_id, error=err, generation=generation,
                                  gate_status='failed', gate_error=err,
                                  fencing_token=fencing_token)
                self._notify_scan(session_row, None, ok=False)
                self._notify_child_done(
                    session_row, False, elapsed=time.monotonic() - turn_start,
                    error=err)
                return
            self._mark_done(sid, batch_id, last_preview=preview,
                            generation=generation, gate_status=gate_status,
                            fencing_token=fencing_token)
            self._notify_scan(session_row, final_msg, ok=True)
            self._notify_child_done(
                session_row, True, elapsed=time.monotonic() - turn_start)
            if user_prompt_for_memory is not None:
                self._record_memory(user_id, user_prompt_for_memory, final_msg)
        except _SessionCancelled:
            # 用户主动取消，不发完成通知
            self._mark_cancelled(sid, batch_id, generation=generation,
                                 fencing_token=fencing_token)
            self._notify_scan(session_row, None, ok=False)
        except _SessionPaused:
            # 用户主动暂停：不是失败也不算完成 —— 不占批次计数、不回写扫描
            # 结果（暂停的子任务之后会被 resume 继续跑完）、不发通知。
            self._mark_paused(sid, batch_id, generation=generation,
                              fencing_token=fencing_token)
        except _LeaseLost:
            # 执行权被接管（spec §6.2）：立刻放手——不 abort（接管者可能正在
            # 复用该 OpenCode 会话）、不做任何终态写回（会被 fencing 拒绝），
            # 新 owner 的对账/轮询负责收口。
            logger.warning('batch lease lost sid=%s gen=%s fence=%s; '
                           'dropping without terminal write',
                           sid, generation, fencing_token)
            return
        except (_SessionTimeout, _TurnFailed) as e:
            # 可重试类失败（停滞/工具卡死/部分 provider 错误）先自动重新排队；
            # 预算用尽或不属于白名单才落 failed。
            if self._is_retryable(e) and self._maybe_auto_retry(
                    sid, generation=generation):
                return
            # 两者都已自带可读原因，直接落库；不要加 `{type}: ` 前缀，那对用户是噪音。
            err = str(e)[:500]
            self._mark_failed(sid, batch_id, error=err, generation=generation,
                              fencing_token=fencing_token)
            self._notify_scan(session_row, None, ok=False)
            self._notify_child_done(
                session_row, False, elapsed=time.monotonic() - turn_start, error=err)
        except Exception as e:
            # 网络类异常（OpenCode 不可达等）可自动重试；其余直接失败。
            if isinstance(e, requests.exceptions.RequestException)                     and self._maybe_auto_retry(sid, generation=generation):
                return
            err = f'{type(e).__name__}: {e}'[:500]
            self._mark_failed(sid, batch_id, error=err, generation=generation,
                              fencing_token=fencing_token)
            self._notify_scan(session_row, None, ok=False)
            self._notify_child_done(
                session_row, False, elapsed=time.monotonic() - turn_start, error=err)
        finally:
            # 收尾：无论成功失败，把工作区里已产生的新增/修改文件记进独立表
            # （best-effort，失败不影响子任务本身的状态落库）。
            if ws:
                self._record_workspace_files(sid, ws)
            # P1：usage 累计 + 预算判定 + turn_complete checkpoint（全 best-effort，
            # 失败只记日志——观测/预算绝不反过来打断执行收口）。
            try:
                from utils import execution_budget, execution_checkpoint
                usage = None
                try:
                    from utils.batch_repo import get_session_usage
                    usage = get_session_usage(sid)
                except Exception:
                    usage = None
                execution_budget.accumulate_usage(
                    sid, batch_id=batch_id, usage=usage,
                    wall_clock_ms=int((time.monotonic() - turn_start) * 1000))
                if batch_id:
                    verdict = execution_budget.evaluate_batch_budget(batch_id)
                    if verdict['exceeded']:
                        execution_budget.apply_budget_action(batch_id,
                                                             verdict['action'],
                                                             self)
                execution_checkpoint.write_checkpoint(
                    sid, checkpoint_type='turn_complete',
                    execution_generation=generation,
                    opencode_session_id=locals().get('oc_session_id'))
            except Exception:
                logger.debug('post-terminal P1 bookkeeping failed sid=%s',
                             sid, exc_info=True)

    # --- 动作账本与到位门禁（设计:docs/design/AI子任务动作账本与到位门禁设计.md）---

    def _fetch_action_checks(self, batch_id: str):
        """入口 A/B:批任务自身的 action_checks,未配则回退引用模板上的值。"""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT action_checks, template_id FROM ai_chat_batches "
                    "WHERE id = %s",
                    (batch_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                checks, template_id = row[0], row[1]
                if not checks and template_id:
                    cur.execute(
                        "SELECT action_checks FROM ai_chat_prompt_templates "
                        "WHERE id = %s",
                        (template_id,),
                    )
                    trow = cur.fetchone()
                    checks = trow[0] if trow else None
                return checks or None

    def _register_action_expectations(self, sid: str, batch_id: str,
                                      session_row: dict | None = None) -> dict:
        """派发前把批定义/模板的 action_checks 登记为期望行。带 apply_to
        条件的检查按子任务属性(batch_seq/输入文件名)过滤——不匹配的子任务
        不登记,避免不相关动作的门禁误报。

        P0 fail-closed（spec §8.1）：返回 {'applicable','registered','error'}
        供终态核对判定——登记异常不再"降级为 warning 后继续"，异常时
        error 非空，终态走 inconclusive → failed（不得 completed）。"""
        try:
            checks = self._fetch_action_checks(batch_id)
            if not checks:
                return {'applicable': 0, 'registered': 0, 'error': None}
            seq = (session_row or {}).get('batch_seq')
            infile = (session_row or {}).get('batch_input_file')
            checks = [c for c in checks
                      if agent_ledger.check_applies_to_child(c, seq, infile)]
            if not checks:
                return {'applicable': 0, 'registered': 0, 'error': None}
            registered = agent_ledger.register_session_expectations(
                sid, checks, source='batch', get_db=get_db)
            return {'applicable': len(checks), 'registered': registered,
                    'error': None}
        except Exception as e:  # noqa: BLE001 —— 登记失败必须可见（fail-closed）
            logger.warning('action expectation registration failed sid=%s: %s',
                           sid, e)
            return {'applicable': None, 'registered': 0, 'error': str(e)[:200]}

    def _check_action_gate(self, sid: str) -> dict:
        """终态核对。inconclusive(账本不健康/查询异常)不算失败:任务照常
        完成,但期望行与日志留痕,不静默放行。"""
        healthy = self._ledger_health.pop(sid, True)
        result = agent_ledger.check_session_gate(sid, ledger_healthy=healthy,
                                                 get_db=get_db)
        if result['status'] == 'inconclusive':
            logger.warning('action gate inconclusive sid=%s: %s',
                           sid, result.get('error') or healthy is False)
        return result

    def _batch_gate_retry_flag(self, batch_id: str):
        """批级修正开关(NULL=未设置,跟随全局环境变量)。"""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT gate_retry FROM ai_chat_batches WHERE id = %s",
                            (batch_id,))
                row = cur.fetchone()
        return row[0] if row else None

    def _close_attempt_for_requeue(self, session_id: str, reason: str):
        """重排（gate retry / auto retry / 对账重排）前的 attempt 收口（F9）：
        最新 running attempt 落 'recovering'（不是 completed/failed——该轮
        没有真正收敛），并把 id 记入 _parent_attempt 供下一次 create_attempt
        串 parent_attempt_id 链。"""
        from utils import execution_audit
        closed = execution_audit.finish_latest_running(
            session_id, 'recovering', error_code=reason)
        if closed:
            self._parent_attempt[session_id] = closed

    def _maybe_gate_retry(self, session_id: str, gate: dict,
                          batch_id: str | None = None,
                          generation: int | None = None) -> bool:
        """gate_retry:不过门时带"缺失明细"定向修复提示,在原会话上 continue
        续跑(原 agent、上下文保留,不重做已完成部分)。开关优先级:批级
        gate_retry 列 > 全局环境变量 AI_BATCH_GATE_RETRY(默认 0=关闭);
        批级开启时预算至少 1 次,与 auto-retry 共用 retry_count。"""
        cap = int(os.getenv('AI_BATCH_GATE_RETRY', '0') or 0)
        if batch_id is not None:
            flag = self._batch_gate_retry_flag(batch_id)
            if flag is True:
                cap = max(cap, 1)
            elif flag is False:
                return False
        if cap <= 0:
            return False
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT retry_count, opencode_session_id FROM ai_chat_sessions "
                    "WHERE id = %s", (session_id,))
                row = cur.fetchone()
                if row is None:
                    return False
                retry_count, oc = row
                if retry_count >= cap:
                    return False
                repair = ('上一轮任务已完成,但动作门禁核对未通过:'
                          + agent_ledger.gate_failure_message(gate)
                          + '。请只补齐上述缺失的动作,不要重做已完成的部分;'
                          '完成后正常收尾。')
                cur.execute(
                    "UPDATE ai_chat_sessions "
                    "SET status='pending', retry_count = retry_count + 1, "
                    "    error_message=NULL, cancel_requested=false, pause_requested=false, "
                    "    execution_generation = execution_generation + 1, "
                    "    active_turn_id = NULL, "
                    "    continue_prompt = CASE WHEN %s IS NOT NULL THEN %s "
                    "                      ELSE continue_prompt END "
                    "WHERE id = %s",
                    (oc, repair, session_id))
        self._close_attempt_for_requeue(session_id, 'GATE_RETRY')
        logger.warning('action gate retry sid=%s (re-queued with repair prompt)',
                       session_id)
        return True

    def _record_workspace_files(self, session_id: str, ws: str):
        try:
            changes, _truncated, ok = git_changes(ws)
            if ok:
                record_session_files(session_id, changes)
        except Exception:
            pass  # best-effort

    def _notify_child_done(self, session_row: dict, ok: bool,
                           elapsed: float | None = None, error: str | None = None):
        """批任务每个子会话到达终态时，给任务所有者发一条站内通知（F9）。

        批任务是用户发起后通常会离开的异步作业，所以这里**默认开启**、不依赖
        浏览器开关/通知权限，标签页关闭也不丢——下次打开页面铃铛即显示未读。
        成功与失败都通知（失败恰恰是离开后最需要知道的）；用户主动取消不通知。
        通知 source 指向 ai-chat 会话，铃铛点击即 /ai-chat?session=<sid>。
        best-effort：通知失败不影响子任务状态落库。

        定时扫描（轨迹分析）子会话不在此通知：一次调度会按记录数产出几十上百个
        子会话且按计划反复运行，逐个发铃铛会把通知中心刷爆；其结果通过数据表
        状态回写（ai_scan_engine.on_child_finished）呈现，与交互式路径跳过
        「轨迹分析:」会话的处理保持一致。"""
        try:
            sid = session_row.get('id')
            user_id = session_row.get('user_id')
            # 仅批任务子会话通知；open-api 独立子会话（batch_id 为空）是程序化调用，
            # 不产生界面铃铛通知。
            if not sid or not user_id or not session_row.get('batch_id'):
                return
            # 定时扫描子会话：高并发 + 周期性自动运行，结果另有数据表回写界面，不发铃铛。
            if session_row.get('scan_task_id'):
                return
            title = session_row.get('title') or '子任务'
            dur = _fmt_elapsed(elapsed) if elapsed is not None else ''
            if ok:
                create_notification(
                    user_id,
                    'aiBatchChildDone',
                    f'批任务子任务已完成：{title}',
                    f'耗时 {dur}，点击查看对话。' if dur else '点击查看对话。',
                    source_collection='ai-chat',
                    source_record_id=sid,
                )
            else:
                reason = f'（{error[:80]}）' if error else ''
                create_notification(
                    user_id,
                    'aiBatchChildFailed',
                    f'批任务子任务失败：{title}',
                    f'子任务未成功完成{reason}，点击查看。',
                    source_collection='ai-chat',
                    source_record_id=sid,
                )
        except Exception as e:
            logger.debug('batch child turn-done notification failed sid=%s: %s',
                         session_row.get('id'), e)

    def _persist_provision_notice(self, session_id: str, warning: str,
                                  header: str | None = (
                                      '⚠️ 预置仓库克隆失败，已使用全局 Agent / Skill 继续。')):
        """Insert a notice into the child's thread about workspace provisioning
        (clone failure by default; `header=None` posts `warning` verbatim —
        used for the global-skills injection success notice). Inserted before
        the turn so it sorts to the top. Best-effort."""
        try:
            import uuid as _uuid
            import json as _json
            text = f'{header}\n\n{warning}' if header else warning
            content = [{'type': 'text', 'text': text}]
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

    def _record_memory(self, user_id, user_text, final_msg):
        """Best-effort：把这轮批任务/扫描任务的问答写入用户长期记忆（mem0），
        与交互式聊天的 extract_from_turn 对齐，但走独立的直调路径——
        extract_from_turn 专为真人交互会话设计，会主动跳过 batch/scan 子会话。"""
        if not user_id:
            return
        try:
            from utils.ai_scan_engine import message_text
            assistant_text = message_text(final_msg)
            if not assistant_text:
                return
            from utils.memory import add_memory
            messages = [{'role': 'user', 'content': user_text},
                       {'role': 'assistant', 'content': assistant_text}]
            threading.Thread(target=add_memory, args=(user_id, messages), daemon=True).start()
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
            out = subprocess.run(args, capture_output=True, timeout=180,
                                 creationflags=_NO_WINDOW)
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

    def _recover_session(self, session_id: str, ws: str, prompt: str,
                         agent: str, model: str) -> str:
        """OpenCode session 失效时（send_message 派发失败）的恢复：新建
        session + 注入历史摘要 + 更新绑定 + 重发。返回新的 opencode_session_id。

        镜像 routes/ai_chat.py::_recover_session_and_resend（M3，交互式聊天
        侧的同一个机制），共用 utils/session_history.py 的历史摘要渲染。

        恢复本身失败就直接抛，不重试——调用方 _run_one 的 except Exception
        会接住，落 failed 并带上真实的恢复失败原因；原始的派发失败原因已经
        在调用前被 logger.warning 记下，不会丢失。
        """
        new_oc = opencode_client.create_session(directory=ws)
        self._set_opencode_id(session_id, new_oc, ws)
        history = render_history_block(session_id, exclude_msg_id=f'{session_id}:user')
        opencode_client.send_message(new_oc, (history + prompt).strip(),
                                     directory=ws, agent=agent, model=model)
        return new_oc

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


    @staticmethod
    def _snapshot_assistant_ids(oc_session_id: str, directory: str) -> set:
        """continue 模式派发新提示词前的基线：该 OpenCode 会话里已存在的全部
        assistant 消息 id。_await_finished 只把基线之外的 assistant 消息当作
        本轮的完成/错误判据。OpenCode 不可达时返回空集（退回旧行为——不跳过），
        让 send_message 去报真正的连接错误。"""
        try:
            raw = opencode_client.get_messages(oc_session_id, directory=directory) or []
        except Exception:
            return set()
        return {info.get('id') for m in raw
                if (info := m.get('info') or {}).get('role') == 'assistant'
                and info.get('id')}

    def _await_finished(self, oc_session_id: str, sid: str,
                        directory: str = '',
                        on_progress=None,
                        baseline_ids: set | None = None,
                        lease_owner: str | None = None,
                        fencing_token: int | None = None) -> tuple[str | None, dict | None]:
        """Poll until the latest assistant message reports finished.

        Returns (preview_first_line, full_message_dict). The full message is
        what gets persisted to ai_chat_messages so the user can read the
        conversation via the 查看 button on the batch dashboard.

        `on_progress` (optional) is called at most every PROGRESS_PERSIST_SEC
        while the turn runs, so callers can persist the conversation live off the
        same REST poll. Best-effort: an error there never aborts the wait.

        `sid` (the child's own ai_chat_sessions.id) is checked once per poll for
        cancel_requested — a caller-triggered cancel. When set, aborts the
        OpenCode session (best-effort) and raises _SessionCancelled so _run_one
        can mark the child 'cancelled' instead of waiting out the full timeout.

        `baseline_ids` (continue mode): assistant message ids that existed
        BEFORE this turn's prompt was dispatched. The previous turn's last
        message is still terminal (completed, or aborted-with-error) until the
        new turn's first message shows up — without the baseline the first poll
        would read that stale message and instantly "finish" (or _TurnFailed)
        a turn that just started.

        卡死防线（无人值守三件套，见类常量注释）：
        - question 自动拒绝：模型用 question 工具提问会挂起回合，周期性检查并
          自动拒绝（_reject_pending_questions）；
        - 工具卡死看门狗：工具调用的签名冻结超过 TOOL_STALL_TIMEOUT_SEC 且
          没有活跃子代理作保 → abort + 失败（reason='tool stuck'）；
        - 原有 STALL 看门狗不变：工具在跑就算进展，只有真冻结才触发。
        """
        cap = self.SESSION_TIMEOUT_SEC
        deadline = (time.time() + cap) if cap and cap > 0 else None   # None = no hard cap
        last_preview = None
        last_message = None
        last_sig = None
        last_progress_at = time.time()
        last_persist_at = 0.0
        last_q_check = 0.0
        tool_stall_start: float | None = None
        child_sigs: dict = {}
        first = True
        last_hb_at = 0.0
        while deadline is None or time.time() < deadline:
            # P1 子会话租约心跳（spec §6.2）：每 HEARTBEAT_SEC 续租一次；
            # rowcount=0 → 执行权已被接管，立刻 abort 并放弃本回合。
            if lease_owner and fencing_token is not None \
                    and time.time() - last_hb_at >= 20:
                last_hb_at = time.time()
                if not self._renew_child_lease(sid, lease_owner, fencing_token):
                    try:
                        opencode_client.abort_session(oc_session_id,
                                                      directory=directory)
                    except Exception:
                        pass
                    raise _LeaseLost()
            stop = self._stop_requested(sid)
            if stop:
                try:
                    opencode_client.abort_session(oc_session_id, directory=directory)
                except Exception:
                    traceback.print_exc()
                raise _SessionCancelled() if stop == 'cancel' else _SessionPaused()
            msgs = opencode_client.list_messages(oc_session_id,
                                                 directory=directory) or []
            now = time.time()
            if now - last_q_check >= self.QUESTION_CHECK_INTERVAL:
                last_q_check = now
                self._reject_pending_questions(oc_session_id, directory=directory)
            if on_progress and now - last_persist_at >= self.PROGRESS_PERSIST_SEC:
                try:
                    on_progress()
                except Exception:
                    traceback.print_exc()
                last_persist_at = time.time()
            active_tool = False
            for m in reversed(msgs):
                if m.get('role') == 'assistant':
                    if baseline_ids and m.get('id') in baseline_ids:
                        continue  # message from before this (continued) turn
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
            # Watchdogs. 签名变化 = 有进展：重置两个计时器。工具在跑但父级输出
            # 冻结时，no-progress 看门狗保持安静（既有限制），同时给这个纹丝
            # 不动的工具调用单独计时（tool-stuck 看门狗）。
            sig = self._progress_signature(msgs)
            if first or sig != last_sig:
                first = False
                last_sig = sig
                last_progress_at = now
                tool_stall_start = now if active_tool else None
            elif active_tool:
                last_progress_at = now
                if (self.TOOL_STALL_TIMEOUT_SEC > 0
                        and tool_stall_start is not None
                        and now - tool_stall_start > self.TOOL_STALL_TIMEOUT_SEC):
                    if self._subagent_progressing(msgs, baseline_ids, directory,
                                                  child_sigs):
                        tool_stall_start = now   # 子代理还活着：顺延等待
                    else:
                        # 卡死的工具调用：先 abort（别让 OpenCode 继续空转烧
                        # token）再失败，带着可读原因。
                        try:
                            opencode_client.abort_session(oc_session_id, directory=directory)
                        except Exception:
                            traceback.print_exc()
                        raise _SessionTimeout(int(now - tool_stall_start),
                                              reason='tool stuck')
            elif now - last_progress_at > self.STALL_TIMEOUT_SEC:
                raise _SessionTimeout(int(now - last_progress_at),
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
                    if p.get('type') in ('text', 'reasoning'):
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
            # OpenCode 有时把一段连续推理拆成很多个短小的 reasoning part
            # （各自独立 id，每个只有一两个 token），不是复用同一个 id 增量
            # 续写——原样一个 part 一个 content 条目会渲成一长串「思考完成」
            # 气泡（同一处理见 chat_persist.py::_flatten_scope）。相邻的直接
            # 拼进上一条，不留分隔符；中间隔了别的 part 类型才算另一段。
            if mapped['type'] == 'reasoning' and out and out[-1]['type'] == 'reasoning':
                out[-1]['text'] += mapped['text']
                continue
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
                              directory: str = '',
                              generation: int | None = None):
        """Persist the FULL conversation: the user prompt + every assistant
        message (mapped to text + tool_use parts) read from OpenCode's REST
        message list, so the batch child's thread shows tool bubbles like an
        interactive session. Falls back to `assistant_msg` if REST yields none.

        Idempotent — the user row uses a deterministic id and each assistant row
        is keyed on its OpenCode message id (ON CONFLICT DO UPDATE), so calling
        this repeatedly while a turn runs upserts the growing conversation
        instead of duplicating it (that's how the live view is driven).
        Best-effort; never raises.

        P0 stale-write 防线（spec §6.3）：传了 generation 时校验子会话仍在
        running 且代数未变——retry/reexecute 换代后，旧 turn 的迟到进度快照
        不再写入，避免旧回合消息污染新回合的对话历史。"""
        if generation is not None:
            from utils import batch_repo
            st = batch_repo.read_child_control_state(session_id)
            if st and (st['status'] != 'running'
                       or st['execution_generation'] != generation):
                logger.info('stale persist skipped sid=%s gen=%s current=%s/%s',
                            session_id, generation, st['status'],
                            st['execution_generation'])
                return
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

            # F1（P0 spec §8.2）：先 upsert 每个子代理自己的 ai_chat_subtasks
            # 行，再写子代理的工具账本——agent_tool_calls.subtask_id 外键指向
            # ai_chat_subtasks(id)，旧顺序（先账本后建行）让首次出现的子代理
            # INSERT 违反 FK、账本缺口 → 门禁被误判 inconclusive。先落行/
            # 消息再落顶层，占位气泡不会有哪一层状态落后。
            for sid, info in known.items():
                self._write_subtask(session_id, sid, info, subtask_status,
                                    child_messages.get(sid, []), subtask_id_map)

            # 动作账本（设计 §5.1）：根会话与每个子代理的 tool part 落账。
            # best-effort：失败置健康标记，终态门禁按 inconclusive 处理，
            # 不算未到位也不静默放行。
            ledger_ok = agent_ledger.record_messages(
                oc_session_id, raw, root_session_id=session_id,
                get_db=get_db)
            for child_sid, child_msgs in child_messages.items():
                child_agent = (known.get(child_sid) or {}).get('agent')
                if not agent_ledger.record_messages(
                        child_sid, child_msgs, root_session_id=session_id,
                        subtask_id=child_sid, agent_name=child_agent,
                        get_db=get_db):
                    ledger_ok = False
            self._ledger_health[session_id] = ledger_ok

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

    # --- 终态落库（P0 CAS：唯一入口 repo.transition_child，spec §6.1）---

    @staticmethod
    def _resolve_generation(session_id: str, generation: int | None) -> int:
        """写回用的 execution_generation：调用方（_run_one）传 claim 时捕获的
        值；直接调用（旧测试/外部）没传时从 DB 现读——读到什么就用什么，
        由 CAS 的 WHERE 条件保证不匹配时 0 行。"""
        if generation is not None:
            return generation
        from utils import batch_repo
        st = batch_repo.read_child_control_state(session_id)
        return (st or {}).get('execution_generation') or 0

    def _on_transition_miss(self, session_id: str, batch_id: str | None,
                            generation: int | None, attempted: str,
                            *, redirect: bool):
        """CAS 未命中（rowcount=0）的统一处理：子会话已被并发收口或已换代。
        redirect=True（来自 done/failed 写回）时按当前控制标志重定向到
        cancel/pause 落库；redirect=False（cancel/pause 写回自身未命中）只记
        日志即止，绝不递归。"""
        from utils import batch_repo
        st = batch_repo.read_child_control_state(session_id)
        if st is None:
            return  # 行已消失（批被删，FK CASCADE）
        if st['status'] != 'running':
            logger.info('stale terminal write ignored sid=%s attempted=%s '
                        'current=%s (already settled)', session_id, attempted,
                        st['status'])
            return
        if redirect and st.get('cancel_requested'):
            self._mark_cancelled(session_id, batch_id,
                                 generation=st['execution_generation'])
            return
        if redirect and st.get('pause_requested'):
            self._mark_paused(session_id, batch_id,
                              generation=st['execution_generation'])
            return
        logger.info('stale terminal write ignored sid=%s attempted=%s gen=%s',
                    session_id, attempted, generation)

    def _mark_done(self, session_id: str, batch_id: str,
                   last_preview: str | None, generation: int | None = None,
                   gate_status: str | None = None,
                   gate_error: str | None = None,
                   fencing_token: int | None = None):
        """完成落库。CAS（running+generation+无控制标志）命中时同一事务完成
        计数与批次状态重算；未命中按控制标志重定向或忽略，**不**重复计数。"""
        from utils import batch_repo
        res = batch_repo.transition_child(
            session_id, 'completed',
            generation=self._resolve_generation(session_id, generation),
            last_message_preview=last_preview, gate_status=gate_status,
            gate_error=gate_error, count='done', turn_status='completed',
            fencing_token=fencing_token)
        if res is None:
            self._on_transition_miss(session_id, batch_id, generation,
                                     'completed', redirect=True)
            return
        from utils import execution_audit
        execution_audit.finish_latest_running(session_id, 'completed')
        if res.get('batch_status') in ('completed', 'partial', 'failed'):
            _notify_callback(batch_id, res['batch_status'],
                             res.get('callback_url'), res.get('callback_secret'),
                             res.get('done'), res.get('failed'), res.get('total'))

    def _mark_failed(self, session_id: str, batch_id: str, error: str,
                     generation: int | None = None,
                     gate_status: str | None = None,
                     gate_error: str | None = None,
                     fencing_token: int | None = None):
        from utils import batch_repo, execution_audit
        # 本 attempt 确实跑完并失败：无论会话行 CAS 是否命中都收口审计，
        # 不留悬挂 running attempt（F9 的终态侧）。
        execution_audit.finish_latest_running(
            session_id, 'failed', error_code='CHILD_FAILED', error_message=error)
        res = batch_repo.transition_child(
            session_id, 'failed',
            generation=self._resolve_generation(session_id, generation),
            error_message=error, gate_status=gate_status, gate_error=gate_error,
            count='failed', turn_status='failed',
            fencing_token=fencing_token)
        if res is None:
            self._on_transition_miss(session_id, batch_id, generation,
                                     'failed', redirect=True)
            return
        if res.get('batch_status') in ('completed', 'partial', 'failed'):
            _notify_callback(batch_id, res['batch_status'],
                             res.get('callback_url'), res.get('callback_secret'),
                             res.get('done'), res.get('failed'), res.get('total'))

    def _mark_cancelled(self, session_id: str, batch_id: str | None,
                        generation: int | None = None,
                        fencing_token: int | None = None):
        """Same shape as _mark_failed (cancelled counts toward the batch's
        `failed` aggregate — see batch_repo.cancel_batch / _mark_paused for the
        contrast), but writes the literal 'cancelled' status so callers can tell
        a deliberate cancel apart from a genuine error. CAS 带
        cancel_requested=true 期望（spec §6.1：取消按取消条件收口）。"""
        from utils import batch_repo, execution_audit
        execution_audit.finish_latest_running(
            session_id, 'stopped', error_code='CANCELLED')
        res = batch_repo.transition_child(
            session_id, 'cancelled',
            generation=self._resolve_generation(session_id, generation),
            error_message='已被调用方取消', expect_cancel=True,
            # cancel 优先（spec §4.2）：并存 pause 标志不再挡取消收口
            expect_pause=None, fencing_token=fencing_token,
            count='failed', turn_status='cancelled')
        if res is None:
            self._on_transition_miss(session_id, batch_id, generation,
                                     'cancelled', redirect=False)

    def _mark_paused(self, session_id: str, batch_id: str | None,
                     generation: int | None = None,
                     fencing_token: int | None = None):
        """协作式暂停的落库：status='paused'。与 _mark_cancelled 的区别是**不占
        failed 计数**（暂停不是失败，批次还能整体 resume），error_message 留空。
        CAS 带 pause_requested=true 且 cancel 优先（spec §4.2：cancel > pause）。"""
        from utils import batch_repo, execution_audit
        execution_audit.finish_latest_running(session_id, 'stopped',
                                              error_code='PAUSED')
        res = batch_repo.transition_child(
            session_id, 'paused',
            generation=self._resolve_generation(session_id, generation),
            expect_pause=True, turn_status='paused',
            fencing_token=fencing_token)
        if res is None:
            self._on_transition_miss(session_id, batch_id, generation,
                                     'paused', redirect=False)

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        """失败分类：是否值得自动重试。

        可重试：停滞/工具卡死（_SessionTimeout 的两个 watchdog 原因）、
        provider 瞬时错误（RETRYABLE_TURN_FAILED 白名单）、网络异常。
        不可重试：硬超时上限（长任务被时钟杀掉，续跑只会更大）、
        ProviderAuthError（密钥错，重试必然再炸）、用户中断、上下文超限，
        以及一切白名单之外的未知失败（保守不重试）。
        """
        if isinstance(exc, _SessionTimeout):
            return exc.reason in ('stalled (no progress)', 'tool stuck')
        if isinstance(exc, _TurnFailed):
            return exc.name in BatchWorker.RETRYABLE_TURN_FAILED
        if isinstance(exc, requests.exceptions.RequestException):
            return True
        return False

    def _maybe_auto_retry(self, session_id: str,
                          generation: int | None = None) -> bool:
        """可重试失败时把子任务重新排队（状态回 pending，不占批次计数）。

        已开跑过的（有 OpenCode 会话）置 continue_prompt 走 continue 模式，
        在原会话/原工作区上续跑；从未开跑的原样重排。预算（MAX_AUTO_RETRY）
        从 DB 现读——claim 行里的快照在本轮已过期。预算用尽返回 False，由
        调用方走既有 failed 路径。"""
        if self.MAX_AUTO_RETRY <= 0:
            return False
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT retry_count, opencode_session_id FROM ai_chat_sessions "
                    "WHERE id = %s", (session_id,))
                row = cur.fetchone()
                if row is None:
                    return False
                retry_count, oc = row
                if retry_count >= self.MAX_AUTO_RETRY:
                    return False
                cur.execute(
                    "UPDATE ai_chat_sessions "
                    "SET status='pending', retry_count = retry_count + 1, "
                    "    error_message=NULL, cancel_requested=false, pause_requested=false, "
                    "    execution_generation = execution_generation + 1, "
                    "    active_turn_id = NULL, "
                    "    continue_prompt = CASE WHEN %s IS NOT NULL THEN %s "
                    "                      ELSE continue_prompt END "
                    "WHERE id = %s",
                    (oc, self.AUTO_RETRY_CONTINUE_PROMPT, session_id))
        self._close_attempt_for_requeue(session_id, 'AUTO_RETRY')
        logger.warning('batch auto-retry sid=%s attempt=%d/%d (re-queued as pending%s)',
                       session_id, retry_count + 1, self.MAX_AUTO_RETRY,
                       ', continue on same opencode session' if oc else '')
        self.notify()  # 立刻唤醒调度器接续
        return True

    @staticmethod
    def _renew_child_lease(session_id: str, lease_owner: str,
                           fencing_token: int) -> bool:
        """子会话租约续租（spec §6.2）。owner/fencing 不匹配 → False。"""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_chat_sessions "
                    "   SET heartbeat_at = NOW(), "
                    "       lease_until = NOW() + (%s || ' seconds')::interval "
                    " WHERE id = %s AND lease_owner = %s "
                    "   AND fencing_token = %s",
                    (str(execution_lease_ttl()), session_id, lease_owner,
                     fencing_token),
                )
                ok = cur.rowcount > 0
            conn.commit()
        return ok

    def _mark_needs_review(self, session_id: str, batch_id: str | None,
                           reason: str, generation: int | None = None):
        """needs_review 终态（P1 spec §4.2）：无法自动安全继续，需人工判断，
        不得自动重放。计入 failed 聚合（与 cancelled 同桶），子任务行保留
        字面 'needs_review' 状态供管理面展示。"""
        from utils import batch_repo, execution_audit
        execution_audit.finish_latest_running(
            session_id, 'stopped', error_code='NEEDS_REVIEW',
            error_message=reason)
        res = batch_repo.transition_child(
            session_id, 'needs_review',
            generation=self._resolve_generation(session_id, generation),
            error_message=reason, count='failed',
            turn_status='cancelled')
        if res is None:
            self._on_transition_miss(session_id, batch_id, generation,
                                     'needs_review', redirect=False)
            return
        if res.get('batch_status') in ('completed', 'partial', 'failed'):
            _notify_callback(batch_id, res['batch_status'],
                             res.get('callback_url'), res.get('callback_secret'),
                             res.get('done'), res.get('failed'), res.get('total'))

    def _reconcile_stale_running(self) -> None:
        """运行中对账器（P1 恢复决策，spec §4.2/§6.3）。

        与 P0 的差异：孤儿判定不再只看本进程线程表——先看子会话租约：
          - lease_until > NOW() → 有人在工作（可能是另一实例），不重排不误杀；
          - lease 过期 / NULL（legacy 行）→ 按恢复决策表分流：
              * OpenCode 会话 404 → 带准确原因失败（沿用 P0 语义）；
              * OpenCode 不可达 → 跳过本轮；
              * 存在 unknown 副作用 → needs_review（禁止自动重放）；
              * OpenCode 会话存在 + 有 checkpoint → continue（原会话续跑，
                注入续跑提示词，generation 不变）；
              * OpenCode 会话存在 + 无 checkpoint → 预算内原地续跑（沿用
                旧行为；无进度证据的重放边界由容灾文档 §8.1 声明）；
              * 无 oc 会话 → 原样重排（尚未开跑）。

        每次接管写 ai_batch_events('child.recovered')。与 dispatcher 同线程，
        天然避开 claim→入账窗口的竞态；任何异常只记日志。"""
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, batch_id, opencode_session_id, workspace_path, "
                        "lease_until FROM ai_chat_sessions "
                        "WHERE status = 'running' "
                        "  AND (batch_id IS NOT NULL OR api_key_id IS NOT NULL)")
                    rows = cur.fetchall()
        except Exception:
            logger.exception('reconcile query failed')
            return
        with self._lock:
            tracked = set(self._running_session_ids)
        for sid, batch_id, oc, ws, lease_until in rows:
            if sid in tracked:
                continue  # 本进程正在跑，轮询循环自己负责
            if lease_until is not None:
                from datetime import datetime, timezone
                try:
                    remaining = lease_until - datetime.now(timezone.utc)
                    if lease_until.tzinfo is None:
                        remaining = lease_until - datetime.now()
                    if remaining.total_seconds() > 0:
                        continue  # 租约未过期：原 owner 仍可能在工作
                except TypeError:
                    pass
            try:
                from utils import execution_effect
                if execution_effect.has_unknown_effects(sid):
                    self._mark_needs_review(
                        sid, batch_id,
                        '存在结局未知的副作用（unknown effect），'
                        '已停止自动执行，请人工复核后重试或重执行')
                    self._emit_recovered(batch_id, sid, 'needs_review')
                    continue
                if not oc:
                    # 丢失发生在 create_session 之前：原样重排（全新执行）
                    self._requeue_lost(sid, continue_on_same=False,
                                       batch_id=batch_id)
                    self._emit_recovered(batch_id, sid, 'requeue_fresh')
                    continue
                try:
                    opencode_client.get_messages(oc, directory=ws or '')
                except requests.exceptions.HTTPError as e:
                    status = e.response.status_code if e.response is not None else None
                    if status == 404:
                        self._mark_failed(
                            sid, batch_id,
                            error='对账器发现 OpenCode 会话已失效'
                                  '（服务端可能重启或清理过该会话），请重试或继续执行')
                        self._emit_recovered(batch_id, sid, 'opencode_404')
                    continue  # 其余 HTTP 状态：本轮跳过
                except requests.exceptions.RequestException:
                    continue  # OpenCode 整体不可达：不批量误杀
                self._requeue_lost(sid, continue_on_same=True, batch_id=batch_id)
                self._emit_recovered(batch_id, sid, 'requeue_continue')
            except Exception:
                logger.exception('reconcile row failed sid=%s', sid)

    def _emit_recovered(self, batch_id: str | None, sid: str, decision: str):
        if batch_id:
            from utils import batch_events
            batch_events.append_event(
                batch_id, 'child.recovered', aggregate_type='child',
                aggregate_id=sid, payload={'decision': decision})

    def _requeue_lost(self, session_id: str, *, continue_on_same: bool,
                      batch_id: str | None):
        """对账器把丢失的 running 行重新排队（retry_count 预算内，超限则失败）。"""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT retry_count FROM ai_chat_sessions WHERE id=%s",
                            (session_id,))
                row = cur.fetchone()
                if row is None:
                    return
                if row[0] >= self.MAX_AUTO_RETRY:
                    self._mark_failed(
                        session_id, batch_id,
                        error='对账器发现子任务的工作线程丢失，且自动重试预算已用尽')
                    return
                cur.execute(
                    "UPDATE ai_chat_sessions "
                    "SET status='pending', retry_count = retry_count + 1, "
                    "    error_message=NULL, cancel_requested=false, pause_requested=false, "
                    "    execution_generation = execution_generation + 1, "
                    "    active_turn_id = NULL, "
                    "    continue_prompt = CASE WHEN %s THEN %s ELSE continue_prompt END "
                    "WHERE id = %s AND status = 'running'",
                    (continue_on_same, self.AUTO_RETRY_CONTINUE_PROMPT, session_id))
                requeued = cur.rowcount > 0
            conn.commit()
        if requeued:
            self._close_attempt_for_requeue(session_id, 'RECONCILE_REQUEUE')
            logger.warning('reconcile re-queued lost running session sid=%s '
                           '(continue_on_same=%s)', session_id, continue_on_same)
            if batch_id is not None:
                _recompute_batch_status(batch_id)
            self.notify()

    @staticmethod
    def _stop_requested(session_id: str) -> str | None:
        """一次查询同时取两个协作式停止标志：'cancel'（中断，落 cancelled/
        failed 计数）或 'pause'（暂停，落 paused/不占计数），都没有则 None。
        取消优先：两个标志理论上互斥（pause 与 cancel 是互斥的用户动作），
        万一同时置位按取消处理。"""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cancel_requested, pause_requested "
                    "FROM ai_chat_sessions WHERE id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        if row[0]:
            return 'cancel'
        if row[1]:
            return 'pause'
        return None

    def _reject_pending_questions(self, oc_session_id: str, directory: str = ''):
        """无人值守兜底：模型调用 question 工具向用户提问会把回合无限期挂起
        （批任务没有用户能回答）。发现本会话的 pending 问题就自动拒绝 ——
        OpenCode 把该工具调用标记为 rejected 并让模型继续（与交互聊天里用户
        点「拒绝」是同一条机制）。全部防御式包装：OpenCode 不可达或返回畸形
        数据都直接跳过，绝不因这个辅助检查打断主轮询。"""
        try:
            pending = opencode_client.list_questions(directory=directory)
            items = [q for q in (pending or []) if isinstance(q, dict)]
        except Exception:
            return
        for q in items:
            if q.get('sessionID') != oc_session_id or not q.get('id'):
                continue
            try:
                opencode_client.reject_question(q['id'], directory=directory)
                logger.info('batch auto-rejected question oc=%s qid=%s',
                            oc_session_id, q['id'])
            except Exception:
                traceback.print_exc()

    @staticmethod
    def _running_task_children(msgs: list, baseline_ids: set | None) -> list:
        """最新（非基线）assistant 消息里仍在运行、且带子代理会话 id 的 task
        工具列表 —— 工具卡死看门狗的"子代理作保"候选。"""
        for m in reversed(msgs):
            if m.get('role') != 'assistant':
                continue
            if baseline_ids and m.get('id') in baseline_ids:
                continue
            return [p.get('child_sid') for p in (m.get('content') or [])
                    if p.get('type') == 'tool_use' and p.get('name') == 'task'
                    and p.get('status') in (None, '', 'pending', 'running')
                    and p.get('child_sid')]
        return []

    def _subagent_progressing(self, msgs: list, baseline_ids: set | None,
                              directory: str, state: dict) -> bool:
        """冻结窗口内，在跑的 task 工具背后的子代理是否仍有产出。

        父会话的消息签名在子代理工作期间是不变的（子代理的输出要等工具返回
        才进父级消息），所以用子代理**自己的**消息列表签名判断存活：签名变了
        = 活着 → 看门狗顺延；没有任何在跑的 task 工具 → False（让卡死的普通
        工具调用被杀）。查不到子代理消息时按"无法判断"处理（宁可多等一个
        窗口），绝不因辅助检查的抖动误杀长跑子代理。
        `state` 跨轮询持有每个子代理上一次的签名（调用方传同一个 dict）。
        """
        children = self._running_task_children(msgs, baseline_ids)
        if not children:
            return False
        progressing = False
        for csid in children:
            try:
                raw = opencode_client.get_messages(csid, directory=directory) or []
                sig = self._raw_progress_signature(raw)
            except Exception:
                progressing = True   # 查不到 ≠ 死了
                continue
            if state.get(csid) is None:
                state[csid] = sig
                progressing = True   # 首次观测：先算活着，下个窗口做对比
            elif sig != state[csid]:
                state[csid] = sig
                progressing = True
        return progressing

    @staticmethod
    def _raw_progress_signature(msgs: list) -> tuple:
        """_progress_signature 的 raw 形态：直接吃 OpenCode REST 消息列表
        ([{'info','parts'}])，供子代理存活检测用（那边不做 facade 映射）。"""
        count = 0
        total_text = 0
        tool_sig: list = []
        for m in msgs:
            info = m.get('info') or {}
            if info.get('role') != 'assistant':
                continue
            count += 1
            for p in (m.get('parts') or []):
                t = p.get('type')
                if t in ('text', 'reasoning'):
                    total_text += len(p.get('text') or '')
                elif t == 'tool':
                    st = p.get('state') or {}
                    out = st.get('output')
                    tool_sig.append((p.get('tool'), st.get('status'),
                                     len(out) if isinstance(out, str) else 0))
        return (count, total_text, tuple(tool_sig))
