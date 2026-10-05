"""StubRuntime —— 压测容量层专用运行时（ai-stress spec §3）。

内存态模拟 agent 生命周期：send_prompt_async 记录派发，完成器线程按 profile
延迟落 assistant 终态消息。消息形状与 opencode_client.get_messages 原始返回
逐字段对齐（{'info','parts'}；完成判据 = info.finish 非 tool-calls 且
info.time.completed 存在——worker list_messages 门控的字段，形状漂移即
worker 永远判未完成，test_runtime_stub 的对照测试锁住）。
另含内存事件总线 subscribe_events(directory)（OpenCode /event 的 stub
等价物：晚订阅不重放、按 directory 过滤、props.sessionID 为路由键），
test_runtime_event_bus 锁其形状。
"""
import queue
import random
import threading
import time
import uuid

from utils.runtime.base import AgentRuntime

DEFAULT_PROFILE = {'delay_ms': [2000, 8000], 'error_rate': 0.0,
                   'hang_rate': 0.0, 'hang_recover_after_ms': 0,
                   'tool_parts': 0}


def parse_profile(raw: dict | None) -> dict:
    p = {**DEFAULT_PROFILE, **(raw or {})}
    p['delay_ms'] = [int(p['delay_ms'][0]), int(p['delay_ms'][1])]
    for k in ('error_rate', 'hang_rate'):
        p[k] = float(p[k])
    p['hang_recover_after_ms'] = int(p['hang_recover_after_ms'])
    p['tool_parts'] = int(p.get('tool_parts', 0) or 0)
    return p


class _Session:
    __slots__ = ('messages', 'pending_until', 'outcome', 'done',
                 'hang_until', 'directory')
    def __init__(self):
        self.messages = []          # raw OpenCode 形状
        self.pending_until = None
        self.outcome = 'stop'       # 'stop' | 'error'
        self.done = False
        self.hang_until = 0.0
        self.directory = ''


class StubClient:
    """batch_engine._OpenCodeFacade 经 get_runtime().get_client() 消费的面。"""

    def __init__(self, profile: dict):
        self.profile = profile
        self._sessions: dict[str, _Session] = {}
        self._subs: list[tuple[str, queue.SimpleQueue]] = []   # (directory, q)
        self._lock = threading.Lock()
        self._completer = threading.Thread(target=self._complete_loop,
                                           daemon=True)
        self._completer.start()

    def create_session(self, *, directory: str, title: str = '') -> str:
        sid = f'stub_{uuid.uuid4().hex}'
        s = _Session()
        s.directory = directory
        with self._lock:
            self._sessions[sid] = s
        return sid

    def send_prompt_async(self, sid: str, content: str, model: str = '',
                          directory: str = '', agent: str = '',
                          agent_parts=None) -> None:
        with self._lock:
            s = self._sessions[sid]
            s.messages.append({'info': {'role': 'user', 'time': {}},
                               'parts': [{'type': 'text', 'text': content}]})
            lo, hi = self.profile['delay_ms']
            s.done = False
            s.pending_until = time.time() + random.uniform(lo, hi) / 1000
            s.outcome = 'error' if random.random() < self.profile['error_rate'] \
                else 'stop'
            if random.random() < self.profile['hang_rate']:
                s.hang_until = time.time() + \
                    self.profile['hang_recover_after_ms'] / 1000

    def get_messages(self, sid: str, directory: str = '') -> list:
        with self._lock:
            return [dict(m) for m in self._sessions[sid].messages]

    def abort_session(self, sid: str, directory: str = '') -> None:
        with self._lock:
            s = self._sessions[sid]
            s.pending_until = None
            s.hang_until = 0.0
            self._append_terminal(sid, s, 'aborted')

    def delete_session(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    # ---- 事件总线 ----
    def subscribe_events(self, directory: str = '', read_timeout=None):
        """按 directory 过滤的事件流；晚订阅不重放（对齐 OpenCode 语义）。

        Yield {'event': <type>, 'data': {'type': <type>, 'properties': {...}}}，
        与 opencode_client.subscribe_events 的帧形状一致；properties.sessionID
        为 apply_event 的路由键，由 _emit 统一写入。
        """
        q: queue.SimpleQueue = queue.SimpleQueue()
        with self._lock:
            self._subs.append((directory or '', q))
        try:
            while True:
                try:
                    etype, props = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                yield {'event': etype,
                       'data': {'type': etype, 'properties': props}}
        finally:
            with self._lock:
                self._subs = [(d, qq) for d, qq in self._subs if qq is not q]

    def _emit(self, sid: str, s: _Session, etype: str, props: dict) -> None:
        """向匹配 directory 的订阅者投递一帧；sessionID 路由键在此统一写入，
        绝不允许空串（apply_event 按它路由到会话）。"""
        props = dict(props or {})
        props['sessionID'] = sid
        for d, q in list(self._subs):
            if d == '' or d == s.directory:
                q.put((etype, props))

    # ---- 完成时间线（REST 视图与事件视图消费同一事实；abort 语义保持）----
    def _append_terminal(self, sid: str, s: _Session, finish: str) -> None:
        now_ms = int(time.time() * 1000)
        msg_id = f'msg_{uuid.uuid4().hex}'
        self._emit(sid, s, 'message.updated',
                   {'info': {'id': msg_id, 'role': 'assistant',
                             'time': {'created': now_ms}}})
        parts = []
        for i in range(int(self.profile.get('tool_parts', 0) or 0)):
            part = {'id': f'{msg_id}-tool{i}', 'messageID': msg_id,
                    'type': 'tool', 'tool': 'bash',
                    'state': {'status': 'completed',
                              'input': {'command': f'stub-cmd-{i}'},
                              'output': 'ok'}}
            parts.append(part)
            self._emit(sid, s, 'message.part.updated', {'part': part})
        text_part = {'id': f'{msg_id}-text', 'messageID': msg_id,
                     'type': 'text', 'text': f'STUB-DONE {finish}'}
        parts.append(text_part)
        self._emit(sid, s, 'message.part.updated', {'part': text_part})
        s.messages.append({
            'info': {'role': 'assistant', 'finish': finish,
                     'time': {'completed': now_ms}},
            'parts': parts,
        })
        if finish == 'stop':
            self._emit(sid, s, 'session.idle', {})
        else:
            self._emit(sid, s, 'session.error',
                       {'error': {'message': f'STUB-TERMINAL {finish}'}})

    def _complete_loop(self) -> None:
        while True:
            time.sleep(0.02)
            with self._lock:
                for sid, s in self._sessions.items():
                    if s.done or s.pending_until is None:
                        continue
                    now = time.time()
                    if now < s.hang_until:
                        continue
                    if now >= s.pending_until:
                        s.pending_until = None
                        s.done = True
                        self._append_terminal(sid, s, s.outcome)


class StubRuntime(AgentRuntime):
    kind = 'stub'

    def __init__(self, profile: dict | None = None):
        if profile is None:
            import json as _json, os as _os
            raw = _os.getenv('AI_STUB_PROFILE', '')
            profile = _json.loads(raw) if raw.strip() else None
        self.profile = parse_profile(profile)
        self._client = StubClient(self.profile)

    def capabilities(self) -> dict:
        return {'kind': 'stub', 'checkpoint': False, 'pause': False,
                'network_isolation': True}

    def create_session(self, directory: str, title: str = '') -> str:
        return self._client.create_session(directory=directory, title=title)

    def dispatch(self, oc_session_id: str, prompt: str, *, directory: str = '',
                 agent: str = '', model: str = '') -> None:
        self._client.send_prompt_async(oc_session_id, prompt,
                                       directory=directory, agent=agent,
                                       model=model)

    def list_messages(self, oc_session_id: str, directory: str = '') -> list:
        return self._client.get_messages(oc_session_id, directory=directory)

    def get_messages(self, oc_session_id: str, directory: str = '') -> list:
        return self._client.get_messages(oc_session_id, directory=directory)

    def abort(self, oc_session_id: str, directory: str = '') -> None:
        self._client.abort_session(oc_session_id, directory=directory)

    def health(self) -> dict:
        return {'ok': True, 'kind': 'stub'}

    def get_client(self) -> StubClient:
        return self._client
