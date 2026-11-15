import logging
import os

import requests

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

    def _client(self, oc_session_id: str = '', kind: str = ''):
        # OC2 双栈（阶段四）：按会话绑定栈路由——查到 oc_runtime 绑定才分流
        # （'v2' → OpenCodeClientV2 + 事件桥）；查不到绑定/异常时走原有
        # get_runtime() 链（保持既有测试打桩点与 v1 单栈行为逐字节等价）。
        # P2 §8.1 / P3-A8 Runtime Adapter 生产接线：runtime 不可用时逐级
        # 回退（adapter get_client → _client → 直连），语义不变。
        if kind:
            try:
                from ai.runtime import registry
                return registry.runtime_for(kind).get_client()
            except Exception:
                pass
        if oc_session_id:
            try:
                from ai.runtime import registry as _reg
                _k = _reg.runtime_kind_by_oc_id(oc_session_id)
                if _k:
                    return _reg.runtime_for(_k).get_client()
            except Exception:
                pass
        try:
            from ai.runtime import get_runtime
            rt = get_runtime()
        except Exception:
            rt = None  # runtime 不可用/未启用 → 走下方兜底直连
        if rt is not None:
            getter = getattr(rt, 'get_client', None)
            if callable(getter):
                return getter()
            inner = getattr(rt, '_client', None)  # 旧 adapter 无 get_client 的兼容回退
            if callable(inner):
                return inner()
        from ai.opencode.opencode_client import OpenCodeClient
        from config import OPENCODE_BASE_URL
        return OpenCodeClient(OPENCODE_BASE_URL)

    def create_session(self, directory: str, title: str = '',
                       kind: str = '') -> str:
        """Create an OpenCode session bound to `directory`; return its id.

        `kind`：批子会话创建时由子会话行携带的绑定栈（oc_runtime 列）；
        空串走原兜底链（v1 单栈行为不变）。"""
        return self._client(kind=kind).create_session(directory=directory,
                                                      title=title)

    def send_message(self, oc_session_id: str, prompt: str,
                     directory: str = '', agent: str = '', model: str = '') -> dict:
        """Fire the prompt asynchronously.  Returns a stub dict so callers can
        discard the return value — the real work happens on the SSE stream.

        `model` ("<providerID>/<modelID>") is the per-batch model; empty falls
        back to the global default chat model (which itself may be empty, leaving the
        choice to OpenCode / the agent default).
        """
        from config import get_default_chat_model
        self._client(oc_session_id).send_prompt_async(
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

        A transient REST error is reported as a poll_error marker so the poll
        loop retries without mistaking it for "no progress"; a persistent
        failure is surfaced as `poll unreachable` by _await_finished (REST
        不可达 ≠ 会话无进展——此前两者同形，网络故障会被伪装成 stalled).
        """
        try:
            raw = self._client(oc_session_id).get_messages(oc_session_id, directory=directory) or []
        except requests.RequestException:
            return [{'role': 'assistant', 'finished': False, 'content': [],
                     'poll_error': 'rest'}]
        if not raw:
            # 消息列表完全为空（连 user 消息都没有——send 之后正常必有）：
            # 目录错位 / 会话不存在，同样是「读不到有效数据」而非「无进展」。
            return [{'role': 'assistant', 'finished': False, 'content': [],
                     'poll_error': 'empty'}]

        out: list = []
        for m in raw:
            info = m.get('info') or {}
            if info.get('role') != 'assistant':
                continue
            parts = m.get('parts') or m.get('content') or []
            content: list = []
            running_tool = False
            resolved_tool_seen = False
            open_steps = 0
            for p in parts:
                t = p.get('type')
                if t == 'step-start':
                    # step 开始瞬间就落盘（实测与 reasoning part 同毫秒创建，
                    # 而 reasoning 文本直到思考结束才可见）——「未配对的
                    # step-start」是 step 进行中唯一的 REST 可见信号。
                    open_steps += 1
                    continue
                if t == 'step-finish':
                    open_steps = max(0, open_steps - 1)
                    continue
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
                        'error': info.get('error'), 'id': info.get('id'),
                        'step_active': open_steps > 0})
        return out or [{'role': 'assistant', 'finished': False, 'content': [],
                        'finish': None, 'running_tool': False, 'error': None,
                        'id': None, 'step_active': False}]

    def get_messages(self, oc_session_id: str, directory: str = '') -> list:
        """Raw OpenCode message list (each {'info':..., 'parts':[...]}). Used by
        _persist_conversation to store the FULL conversation incl. tool parts."""
        return self._client(oc_session_id).get_messages(oc_session_id, directory=directory) or []

    def list_agents(self, directory: str = '') -> list:
        """OpenCode agents available in `directory`'s scope (global + project),
        each {'name','mode',...}. Used to validate a batch's chosen agent."""
        return self._client().list_agents(directory=directory) or []

    def abort_session(self, oc_session_id: str, directory: str = '') -> None:
        """Best-effort abort of a running OpenCode session — used by the
        cooperative-cancel check in _await_finished. Same underlying call as
        routes/ai_chat.py's interactive abort endpoint."""
        self._client(oc_session_id).abort_session(oc_session_id, directory=directory)

    def list_questions(self, directory: str = '') -> list:
        """Pending QuestionRequest objects (OpenCode's interactive question
        tool) scoped to `directory` — used by the batch worker to auto-reject
        questions the unattended run can never get answered."""
        return self._client().list_questions(directory=directory)

    def reject_question(self, request_id: str, directory: str = '',
                        oc_session_id: str = '') -> None:
        """Reject a pending QuestionRequest: OpenCode resolves the underlying
        tool call as rejected and the model continues without an answer.

        `oc_session_id`：询问归属的 OC 会话 id（V2 可选）——绑定栈为 v2 时
        须传（V2 form cancel 端点是会话作用域）；v1 路径忽略该参数。"""
        client = self._client(oc_session_id=oc_session_id)
        if oc_session_id:
            try:
                from ai.runtime import registry
                if registry.runtime_kind_by_oc_id(oc_session_id) == 'v2':
                    client.reject_question(request_id, directory=directory,
                                           oc_session_id=oc_session_id)
                    return
            except Exception:
                pass
        client.reject_question(request_id, directory=directory)


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




__all__ = [
    '_OpenCodeFacade',
    '_WORKER',
    'opencode_client',
    'execution_lease_ttl',
    'get_worker',
    'logger',
]


# 包内交叉引用（get_worker 调用期解析 BatchWorker）：放在模块尾部——
# worker.py 顶部反向导入本模块的 opencode_client/execution_lease_ttl，
# 只有本模块先完成定义再触发 worker 初始化，才能双方都不撞半初始化状态。
from .worker import BatchWorker  # noqa: E402
