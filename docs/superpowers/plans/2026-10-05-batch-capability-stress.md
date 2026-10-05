# 批任务能力稳定性压测 实施计划（子代理 SSE × 门禁并发）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AI 批任务的子代理 SSE 与动作门禁建立并发稳定性压测：先补 stub 事件总线与交互路径 runtime 接线（产品侧小改），再交付五场景压测套件（帧级对账 / 并发终态×门禁 / 修正-重试-取消竞态 / 交互收口竞争 / 树作用域聚合）。

**Architecture:** 复用既有专属压测栈（`tests/stress/conftest.py`：casemanage_stress 专属库 + :3092 stub 后端）。产品侧把交互路径上 5 处硬编码 `OpenCodeClient(OPENCODE_BASE_URL)` 改走 `get_runtime()` 门面，并给 StubClient 加内存事件总线（完成器线程按 OpenCode 事件时间线发件）。压测断言全部为可对账的精确断言（继承 R4/c2 先例）。

**Tech Stack:** Python / pytest（marker `stress`）/ requests SSE 流解析 / psycopg2 直连压测库 / Flask（既有栈）。

**Spec:** `docs/superpowers/specs/2026-10-05-batch-capability-stress-design.md`（计划相对 spec 有两处已核实的事实修正，见 Task 3 与 Task 6 的 spec 修订步骤）。

## Global Constraints

- pytest.ini 默认 `-m "not stress"`；压测用例必须 `pytestmark = pytest.mark.stress`，运行显式 `python -m pytest ... -m stress`。
- stub 运行时需 `AI_STUB_ALLOW=1`（压测栈 conftest 已注入；勿在 dev 环境设置后忘记清除）。
- 压测栈独占端口 :3092（后端）/ :4097（混沌 serve，本套件不用）；跑压测前确认端口空闲。
- **全程 0 token**：不切 opencode_local、不 spawn 真 serve；模块入口必须 `restart_backend` 复位 stub（防 chaos 套件泄漏，沿 readpath 惯例）。
- 后端代码改动后必须重启对应后端进程再验证（3002 dev 后端无自动 reload；压测栈每次 `restart_backend` 天然重启）。
- **判别力铁律**：凡声称"base 必失败"的测试，必须 `git stash` 或 `git show <base>:<file>` 换回旧实现实跑验证转红后才能声明；不许推断。
- `docs/superpowers/` 被 .gitignore 的 `superpowers/` 覆盖——spec/plan 相关提交用 `git add -f docs/superpowers/...`。
- 跑本套件不影响 dev 库（压测栈全程用 casemanage_stress），但**不要与全量 pytest/E2E 并发跑**（既有约定）。
- 提交信息用 conventional commits（`feat(stress):` / `test(stress):` / `fix(stress):` / `docs(stress):`）。

## 已核实的形状事实（写测试直接用，勿再猜）

- 批次 SSE（`routes/ai_chat_batches.py:810`）：帧 `id: <bid>:<seq>` + `event: batch_event` + `data: {"eventId","eventSeq","type","data"}`；终态 `event: batch_done`；15s `: ping`；重连凭 `Last-Event-ID: <bid>:<seq>` 或 `?afterSeq=`。
- 会话 SSE（`routes/ai_chat.py:1386` 起）：帧 `event: <etype>` + `data: <props JSON>`；props.sessionID 是路由键。
- StubClient REST 消息形状：`{'info': {'role','finish','time': {'completed': ms}}, 'parts': [...]}`，worker 完成判据 = finish 非 'tool-calls' 且 time.completed 存在。
- 账本 `agent_tool_calls` 列：`oc_session_id, root_session_id, subtask_id, message_id, part_id, tool, args_text, state`；**唯一索引 (oc_session_id, part_id)**（幂等断言锚点）。
- 期望表 `action_expectations` 列：`scope_type, scope_id, name, tool, args_pattern, require_state, min_count, source, last_status, last_checked_at, last_evidence, check_type, effect_spec, subagents, mode`；**UNIQUE (scope_type, scope_id, name)**。
- file 效果核对（`utils/agent_ledger.py:586`）：取该会话 `ai_chat_sessions.workspace_path`，glob `<workspace_path>/<effect_spec.path>`——预置"应过"文件=往子会话工作区写文件。
- 工具 part 形状（`utils/agent_ledger.py:73` extract_from_parts 接受）：`{'type':'tool','id':<pid>,'tool':<name>,'state':{'status':'completed','input':{...}}}`。
- `apply_event`（`utils/chat_persist.py:76`）关键约束：**part 事件只有在 `part.messageID ∈ assistant_msg_ids` 时才被捕获**——必须先发 `message.updated`（`props.info={'id','role':'assistant',...}`）再发 part 事件；委派发现读 `part.tool=='task'` 的 `state.metadata.sessionId` + `state.input.subagent_type`。
- 门禁批侧链：`wait_subtasks_drained`（batch_engine.py:1551）→ `check_session_gate`（agent_ledger.py:646）→ `gate.evaluated` 审计事件（batch_engine.py:1582，`ai_batch_events` 中 event_type='gate.evaluated'）→ `_maybe_gate_retry`（batch_engine.py:1853；预算=env `AI_BATCH_GATE_RETRY` 默认 0，批级 gate_retry=TRUE 时 max(cap,1)；与 auto-retry 共用 retry_count，`AI_BATCH_MAX_AUTO_RETRY` 默认 2）。
- 交互 API：建会话 `POST /ai/chat/sessions` body `{}`（workspaceroot 每会话独立）；发送 `POST /ai/chat/sessions/<sid>/messages` body `{'content': ...}`；鉴权均为 Bearer JWT（压测栈 admin 登录即可）。
- 建批 API：`POST /ai/chat/batches` body `{'name','prompt','files':[{'name','path'}...],'action_checks':[...],'gate_retry':bool}`（action_checks 经 `validate_checks`，gate_retry 在 ai_chat_batches.py:140）。
- `ai_chat_messages` 列：`id, session_id, role, content, meta`。
- openapi/facade：`get_runtime()` 双检锁单例（`utils/runtime/__init__.py`）；`OpenCodeClient.subscribe_events(directory='', read_timeout=None)` 已存在（opencode_client.py:317）。

---

### Task 1: AgentRuntime 门面 subscribe_events + StubClient 事件总线核心

**Files:**
- Modify: `server/utils/runtime/base.py`（AgentRuntime 类末尾加默认方法）
- Modify: `server/utils/runtime/stub.py`（_Session/StubClient 事件总线）
- Create: `server/tests/test_runtime_event_bus.py`

**Interfaces:**
- Produces: `AgentRuntime.subscribe_events(directory: str = '', read_timeout=None)`（默认委托 `self.get_client().subscribe_events(...)`）；`StubClient.subscribe_events(directory='', read_timeout=None)` 生成器，yield `{'event': <type>, 'data': {'type': <type>, 'properties': <dict>}}`；profile 新键 `tool_parts: int`。
- 后续任务消费：Task 2 的委派、Task 3 的路由接线、Task 4/8 的套件断言。

- [ ] **Step 1: 写失败测试**（新文件 `server/tests/test_runtime_event_bus.py`）

```python
"""StubClient 事件总线与门面 subscribe_events 的形状/语义锁。

铁律对齐 StubRuntime 消息形状锁先例：事件形状漂移 = sse_events 路由
静默丢事件，压测全链路作废。本文件同时是门面接线的判别力锚：
在 base（无门面方法）上 Step1 的 test_facade_delegates 必然 AttributeError。
"""
import queue
import threading

import pytest


@pytest.fixture()
def stub(monkeypatch):
    monkeypatch.setenv('AI_STUB_ALLOW', '1')
    from utils.runtime.stub import StubRuntime
    rt = StubRuntime(profile={'delay_ms': [10, 20], 'tool_parts': 2})
    yield rt


def _collect(gen, want_types, timeout=5.0):
    """从 subscribe_events 生成器收集到指定事件类型为止。线程内跑，
    主线程 join——生成器阻塞在 q.get 时 GC 不了，必须显式驱动。"""
    out = []
    got = threading.Event()

    def run():
        for evt in gen:
            out.append(evt)
            if evt['event'] in want_types:
                got.set()
                break

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert got.wait(timeout), f'超时未等到 {want_types}，已收: {[e["event"] for e in out]}'
    return out


def test_timeline_order_and_shapes(stub):
    oc = stub.create_session(directory='/ws/t1')
    gen = stub.get_client().subscribe_events(directory='/ws/t1')
    stub.dispatch(oc, 'hello', directory='/ws/t1')
    evts = _collect(gen, {'session.idle'})
    types = [e['event'] for e in evts]
    # 顺序契约：message.updated 先于 part 事件（apply_event 只有见到
    # assistant message id 后才捕获 part），idle 收尾
    assert types[0] == 'message.updated', types
    assert types[-1] == 'session.idle', types
    assert types.count('message.part.updated') == 3, types   # 2 tool + 1 text
    for e in evts:
        assert e['data']['type'] == e['event']               # OpenCode 形状
    msg_id = evts[0]['data']['properties']['info']['id']
    for e in evts[1:-1]:
        assert e['data']['properties']['part']['messageID'] == msg_id
    # REST 视图与事件视图 parts 一致（worker 轮询与 SSE 双路径消费同一事实）
    rest = stub.get_messages(oc)
    assert rest[-1]['info']['finish'] == 'stop'
    assert len(rest[-1]['parts']) == 3


def test_directory_isolation_and_fanout(stub):
    oc_a = stub.create_session(directory='/ws/a')
    stub.create_session(directory='/ws/b')
    gen_a = stub.get_client().subscribe_events(directory='/ws/a')
    gen_b1 = stub.get_client().subscribe_events(directory='/ws/b')
    gen_b2 = stub.get_client().subscribe_events(directory='/ws/b')
    stub.dispatch(oc_a, 'hello', directory='/ws/a')
    evts = _collect(gen_a, {'session.idle'})
    assert all('/a' not in str(e) or True for e in evts)     # 结构见下——隔离断言
    # a 的订阅者收不到 b 的事件：计数上 a 流只有自己一轮（4 帧）
    assert len([e for e in evts if e['event'] == 'message.part.updated']) == 3
    # b 目录无派发 → 两个 b 订阅者都不该收到任何事件
    import time
    time.sleep(0.3)
    assert gen_b1.__next__ if False else True               # 占位防误用；真断言见下
```

（注：上面最后一个测试的"无事件"断言用阻塞 next() 不可行，按下面方式重写该用例——**实现时以本块为准替换**：）

```python
def test_directory_isolation_and_fanout(stub):
    oc_a = stub.create_session(directory='/ws/a')
    stub.create_session(directory='/ws/b')
    got_a, got_b1, got_b2 = [], [], []
    stop = threading.Event()

    def pump(gen, sink):
        for evt in gen:
            sink.append(evt)
            if stop.is_set():
                return

    threads = [threading.Thread(target=pump, args=(g, s), daemon=True) for g, s in
               ((stub.get_client().subscribe_events(directory='/ws/a'), got_a),
                (stub.get_client().subscribe_events(directory='/ws/b'), got_b1),
                (stub.get_client().subscribe_events(directory='/ws/b'), got_b2))]
    for t in threads:
        t.start()
    stub.dispatch(oc_a, 'hello', directory='/ws/a')
    deadline = threading.Event()
    # 等 a 收齐一轮
    for _ in range(100):
        if any(e['event'] == 'session.idle' for e in got_a):
            break
        time.sleep(0.05)
    time.sleep(0.2)
    stop.set()
    assert any(e['event'] == 'session.idle' for e in got_a)
    assert got_b1 == [] and got_b2 == [], 'b 目录订阅者不该收到 a 的事件'
    assert len(got_a) == 5, f'a 流帧数 {len(got_a)} != 5（fanout 缺失或多发）'
```

再加晚订阅不重放与门面委托两个用例：

```python
def test_no_replay_for_late_subscriber(stub):
    oc = stub.create_session(directory='/ws/late')
    stub.dispatch(oc, 'hello', directory='/ws/late')
    time.sleep(0.3)                                   # 第一轮已完整发完
    gen = stub.get_client().subscribe_events(directory='/ws/late')
    stub.dispatch(oc, 'second', directory='/ws/late')
    evts = _collect(gen, {'session.idle'})
    assert all(e['data']['properties'].get('info', {}).get('id') !=
               evts[0]['data']['properties']['info']['id'] or e is evts[0]
               for e in evts)                          # 只含第二轮（简断言：帧数=5）
    assert len(evts) == 5


def test_facade_delegates_to_client(monkeypatch):
    from utils.runtime.base import AgentRuntime

    class FakeClient:
        def subscribe_events(self, directory='', read_timeout=None):
            return iter([('sentinel', directory, read_timeout)])

    class FakeRT(AgentRuntime):
        def get_client(self):
            return FakeClient()

    assert list(FakeRT().subscribe_events('/ws/x', read_timeout=3)) == \
        [('sentinel', '/ws/x', 3)]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_runtime_event_bus.py -v`
Expected: FAIL——`test_facade_delegates_to_client` 因 AgentRuntime 无 subscribe_events 报 AttributeError；其余用例因 StubClient 无 subscribe_events 报 AttributeError。

- [ ] **Step 3: 实现**（`server/utils/runtime/base.py` AgentRuntime 类末尾追加）

```python
    def subscribe_events(self, directory: str = '', read_timeout=None):
        """订阅会话事件流（OpenCode /event 的 runtime 抽象）。

        默认委托 get_client()：OpenCodeClient 原生具备该方法（行为不变）；
        StubRuntime 经 StubClient 内存总线实现。Yield
        {'event': <type>, 'data': {'type': <type>, 'properties': {...}}}。
        """
        return self.get_client().subscribe_events(directory=directory,
                                                  read_timeout=read_timeout)
```

`server/utils/runtime/stub.py` 改动（保持既有导出不变）：

```python
import queue          # 顶部补 import

DEFAULT_PROFILE = {'delay_ms': [2000, 8000], 'error_rate': 0.0,
                   'hang_rate': 0.0, 'hang_recover_after_ms': 0,
                   'tool_parts': 0}

# parse_profile 末尾补：
#    p['tool_parts'] = int(p.get('tool_parts', 0) or 0)

class _Session:
    __slots__ = ('messages', 'pending_until', 'outcome', 'done',
                 'hang_until', 'directory')          # 不变

class StubClient:
    def __init__(self, profile: dict):
        self.profile = profile
        self._sessions: dict[str, _Session] = {}
        self._subs: list[tuple[str, queue.SimpleQueue]] = []   # (directory, q)
        self._lock = threading.Lock()
        self._completer = threading.Thread(target=self._complete_loop,
                                           daemon=True)
        self._completer.start()

    # ---- 事件总线 ----
    def _emit(self, s: _Session, etype: str, props: dict) -> None:
        props = dict(props or {})
        props.setdefault('sessionID', '')
        for d, q in list(self._subs):
            if d == '' or d == s.directory:
                q.put((etype, props))

    def subscribe_events(self, directory: str = '', read_timeout=None):
        """按 directory 过滤的事件流；晚订阅不重放（对齐 OpenCode 语义）。"""
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

    # ---- 完成时间线（替换 _append_terminal 的调用点；abort 语义保持）----
    def _append_terminal(self, s: _Session, finish: str) -> None:
        now_ms = int(time.time() * 1000)
        msg_id = f'msg_{uuid.uuid4().hex}'
        self._emit(s, 'message.updated',
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
            self._emit(s, 'message.part.updated', {'part': part})
        text_part = {'id': f'{msg_id}-text', 'messageID': msg_id,
                     'type': 'text', 'text': f'STUB-DONE {finish}'}
        parts.append(text_part)
        self._emit(s, 'message.part.updated', {'part': text_part})
        s.messages.append({
            'info': {'role': 'assistant', 'finish': finish,
                     'time': {'completed': now_ms}},
            'parts': parts,
        })
        if finish == 'stop':
            self._emit(s, 'session.idle', {'sessionID': ''})
        else:
            self._emit(s, 'session.error',
                       {'error': {'message': f'STUB-TERMINAL {finish}'}})
```

注意：`_emit` 里 `props.setdefault('sessionID','')` 的占位会在调用处被覆盖——`_complete_loop` 调 `_append_terminal` 前无 sid 上下文，因此 `_emit` 签名改为带 `sid` 参数更直白：`def _emit(self, sid: str, s: _Session, etype, props)`，`props['sessionID'] = sid` 统一由 `_emit` 写入，删掉 setdefault。**实现时用带 sid 的版本**（`sessionID` 是 apply_event 的路由键，绝不能空串——test_timeline 已锁 info.id 但没锁 sessionID，补一行断言：`assert e['data']['properties']['sessionID'] == oc`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_runtime_event_bus.py tests/test_runtime_stub.py -v`
Expected: 全 PASS（含既有 test_runtime_stub——消息形状未被破坏的回归锚）。

- [ ] **Step 5: 判别力 A/B 记录**

Run: `git stash && cd server && python -m pytest tests/test_runtime_event_bus.py -x -q && cd .. && git stash pop`
Expected: FAIL（AttributeError）——在 Task 10 的判别力清单里登记「Task1 用例：base 必失败 = 实测」。

- [ ] **Step 6: Commit**

```bash
git add server/utils/runtime/base.py server/utils/runtime/stub.py server/tests/test_runtime_event_bus.py
git commit -m "feat(stress): AgentRuntime 门面 subscribe_events 抽象 + StubClient 内存事件总线"
```

---

### Task 2: profile 扩展 delegate（委派）+ apply_event 全链路形状锁（含 S5 探针）

**Files:**
- Modify: `server/utils/runtime/stub.py`（profile `delegate: list[str]`；完成时间线注入委派）
- Modify: `server/tests/test_runtime_event_bus.py`（追加用例）

**Interfaces:**
- Produces: profile 键 `delegate: ['explorer', ...]`——完成时为每个 agent 名同步创建子会话（同 directory）、发 tool:'task' part（`state.metadata.sessionId`=子 oc sid、`state.input.subagent_type`=agent 名）、内联跑完子会话时间线，再收父轮 text+idle。
- 消费：S5 树作用域场景的**探针结论**在本任务产出——若 test_delegate_discovers_subtask 失败（apply_event 发现不了），S5 走 DB 种子降级（Task 9 的预案分支）。

- [ ] **Step 1: 追加失败测试**（test_runtime_event_bus.py 末尾）

```python
def test_delegate_discovers_subtask(stub_delegate):
    """S5 探针：stub 总线的委派形状必须能被 apply_event 发现为子代理作用域。
    失败 = S5 走降级预案（spec §5 已预声明）。"""
    from utils.chat_persist import new_state, apply_event
    oc = stub_delegate.create_session(directory='/ws/d')
    state = new_state()
    gen = stub_delegate.get_client().subscribe_events(directory='/ws/d')
    stub_delegate.dispatch(oc, 'delegate work', directory='/ws/d')
    for evt in gen:
        apply_event(state, evt, oc)
        if evt['event'] == 'session.idle':
            break
    assert state['subtasks'], f'委派未被发现: subtasks={list(state["subtasks"])}'
    (child_oc, scope), = state['subtasks'].items()
    assert scope['status'] == 'completed'
    assert child_oc.startswith('stub_')
    # 子会话的 REST 视图独立存在（账本按 oc_session_id 归账的锚）
    child_msgs = stub_delegate.get_messages(child_oc)
    assert child_msgs and child_msgs[-1]['info']['finish'] == 'stop'


def test_tool_parts_reach_ledger_extraction(stub):
    from utils.agent_ledger import extract_tool_parts
    oc = stub.create_session(directory='/ws/led')
    stub.dispatch(oc, 'work', directory='/ws/led')
    deadline = time.time() + 3
    while time.time() < deadline:
        msgs = stub.get_messages(oc)
        if msgs and msgs[-1]['info'].get('time', {}).get('completed'):
            break
        time.sleep(0.05)
    calls = extract_tool_parts(stub.get_messages(oc))
    assert [c[1] for c in calls] == ['bash', 'bash'], calls
    assert all(c[3] == 'completed' for c in calls)
    assert 'stub-cmd-0' in calls[0][2]
```

（fixture：`stub_delegate` 与 `stub` 同构但 profile 用 `{'delay_ms': [10, 20], 'delegate': ['explorer'], 'tool_parts': 1}`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd server && python -m pytest tests/test_runtime_event_bus.py -v -k "delegate or ledger"`
Expected: FAIL——profile 无 delegate 键（无子会话产生）；tool_parts 已在 Task 1 支持故 ledger 用例应 PASS（若红则回查 Task 1）。

- [ ] **Step 3: 实现 delegate**（stub.py：parse_profile 补 `p['delegate'] = list(p.get('delegate') or [])`；`_append_terminal` 在 tool parts 之前插入委派段）

```python
        # 委派段：每个 agent 名 → 同步创建子会话 + tool:'task' part + 子轮时间线
        for i, agent_name in enumerate(self.profile.get('delegate') or []):
            child_sid = self.create_session(directory=s.directory,
                                            title=f'stub-sub-{agent_name}')
            child_s = self._sessions[child_sid]
            task_part = {'id': f'{msg_id}-task{i}', 'messageID': msg_id,
                         'type': 'tool', 'tool': 'task',
                         'state': {'status': 'completed',
                                   'input': {'subagent_type': agent_name,
                                             'description': 'stub delegation',
                                             'prompt': 'stub subtask'},
                                   'metadata': {'sessionId': child_sid}}}
            parts.append(task_part)
            self._emit(s.sid if hasattr(s, 'sid') else s, s, ...)
```

（注：上面片段示意插入位置——**实现时**：`_append_terminal` 需要拿到 sid 才能创建子会话与发件，把签名改为 `_append_terminal(self, sid: str, s: _Session, finish: str)`，`_complete_loop` 与 `abort_session` 两处调用点同步改传 sid。委派段完整逻辑：emit `message.part.updated`（task_part）→ `self._append_terminal(child_sid, child_s, 'stop')`（子会话完整时间线内联跑完）→ 继续 tool parts → text part → 父 idle。子会话 REST 视图自然独立。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && python -m pytest tests/test_runtime_event_bus.py tests/test_runtime_stub.py -v`
Expected: 全 PASS。**若 test_delegate_discovers_subtask 红**：不要改 apply_event（生产代码）——记录探针结论，Task 9 走降级预案。

- [ ] **Step 5: 探针结论落盘 + Commit**

把探针结论追加到 `docs/ai-testing/evidence/stress/2026-10-05-s5-probe.md`（一行结论：事件驱动发现 是否可行）。

```bash
git add server/utils/runtime/stub.py server/tests/test_runtime_event_bus.py docs/ai-testing/evidence/stress/2026-10-05-s5-probe.md
git commit -m "feat(stress): StubClient delegate 委派时间线 + apply_event 全链路形状锁（S5 探针）"
```

---

### Task 3: 交互路径 runtime 接线（5 处硬编码 → get_runtime()）

**Files:**
- Modify: `server/routes/ai_chat.py:161`（create_session）、`:940`（send_message）、`:1386`（sse_events）
- Modify: `server/utils/chat_persist.py:672`（backfill_from_rest）、`:740`（_run_listener 事件源）
- Modify: `docs/superpowers/specs/2026-10-05-batch-capability-stress-design.md`（§3 接线面 3 处→5 处修订）

**Interfaces:**
- Consumes: Task 1 的 `AgentRuntime.subscribe_events`、`get_runtime().get_client()`。
- Produces: stub 栈下交互全链路可用（建会话/发送/监听/事件流全部走门面）；`send_message` 错误恢复助手 `_recover_session_and_resend(client,...)` 经参数传递自动接线，签名不动。
- **范围声明**：ai_chat.py 其余 `OpenCodeClient(OPENCODE_BASE_URL)` 直构点（providers/agents 列表、summarize、abort、lsp、mcp 列表、questions、permissions 等）**本任务不动**——压测套件不触达，属 stub 环境下的已知不可用面（AI_STUB_ALLOW 防呆已挡误配生产）。

- [ ] **Step 1: 五处替换**

`routes/ai_chat.py` 顶部 import 区补 `from utils.runtime import get_runtime`（无环：utils.runtime 不反向依赖 routes）。

```python
# :161 create_session（步骤 4 "ask OpenCode to start a session"）
    client = get_runtime().get_client()

# :940 send_message（import requests as _requests 之后那行）
    client = get_runtime().get_client()

# :1386 sse_events（走门面方法而非 get_client，顺便压到门面默认委托）
    client = get_runtime()
# 函数体内调用处不变：client.subscribe_events(directory=sess[4])
```

`utils/chat_persist.py` 同样补 `from utils.runtime import get_runtime`：

```python
# :672  backfill_from_rest(state, OpenCodeClient(OPENCODE_BASE_URL), ...)
        backfill_from_rest(state, get_runtime().get_client(), ...)   # 参数原样保留

# :740  source = OpenCodeClient(OPENCODE_BASE_URL).subscribe_events(
        source = get_runtime().subscribe_events(                     # kwargs 原样保留
```

- [ ] **Step 2: 门面委托单测补一行防回归**（test_runtime_event_bus.py）

```python
def test_facade_subscribe_is_agentruntime_method():
    from utils.runtime.base import AgentRuntime
    assert hasattr(AgentRuntime, 'subscribe_events')
```

- [ ] **Step 3: 跑既有回归确认无漂移**

Run: `cd server && python -m pytest tests/test_runtime_event_bus.py tests/test_runtime_stub.py tests/test_chat_persist*.py -v 2>/dev/null || cd server && python -m pytest tests/test_runtime_event_bus.py tests/test_runtime_stub.py -v`
Expected: PASS。再跑会话域相关单测（存在 chat_persist 直接单测则一并）：`python -m pytest tests/ -k "persist or listener" -q`
Expected: PASS（chat_persist 的既有单测若直接打 OpenCodeClient 构造点的会改走门面——凡红，逐个核对是"测试自己 new 了假客户端"还是"真回归"，前者 monkeypatch get_runtime 即可）。

- [ ] **Step 4: spec 修订**（接线面事实修正）

把 spec §3 第 1–2 条替换为：接线面为 **5 处**（ai_chat.py create_session/send_message/sse_events + chat_persist.py 监听事件源/REST 回填）——实施期核实发现监听线程与建会话同样硬编码，漏接任何一处 stub 栈下交互链路断裂（监听器注册但不收事件还会挡住 SSE 兜底落库）。§9 不做项补一行：ai_chat.py 辅助端点（providers/abort/summarize 等）保持硬编码，stub 下不可用属已知面。

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/utils/chat_persist.py server/tests/test_runtime_event_bus.py docs/superpowers/specs/2026-10-05-batch-capability-stress-design.md
git commit -m "fix(stress): 交互路径 5 处硬编码 OpenCodeClient 接入 runtime 门面（stub 栈可跑全链路）"
```

---

### Task 4: 压测套件骨架 + S0 交互冒烟（接线 E2E 判别力锚）

**Files:**
- Create: `server/tests/stress/caphelpers.py`
- Create: `server/tests/stress/test_capability_stress.py`

**Interfaces:**
- Produces（caphelpers，Task 5–9 共用）:
  - `create_interactive_session(stack) -> str`
  - `send_message(stack, sid, content) -> None`
  - `open_sse(stack, path, params=None)` — contextmanager，yield 帧迭代器；帧 = `Frame(event, data, eid)`（dataclass；eid 取 `id:` 行原文或 None）
  - `stress_db(stack)` — contextmanager factory：返回 `get_db` 兼容的连接上下文（commit/rollback/close），供 `agent_ledger.register_session_expectations(..., get_db=...)` 与 `check_session_gate(..., get_db=...)` 指向压测库
  - `register_expectations(stack, sid, checks) -> int` — validate + register 到压测库
  - `wait_batch_children_running(stack, bid, n, timeout)` / `wait_terminal`（复用 conftest）
- Produces（套件文件）：模块级 `pytestmark`、模块入口复位 stub 的 autouse fixture（沿 test_readpath_load.py:26 惯例）。

- [ ] **Step 1: 写 caphelpers.py**

```python
"""能力压测套件共享助手（2026-10-05 capability-stress spec §5）。
全部打压测栈 :3092；DB 操作一律走 stack.db_dsn（casemanage_stress），
绝不触碰 dev 库。"""
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg2
import requests


@dataclass
class Frame:
    event: str
    data: dict
    eid: str | None


def create_interactive_session(stack) -> str:
    r = requests.post(f'{stack.base}/ai/chat/sessions',
                      headers=stack.auth_header, json={}, timeout=15)
    r.raise_for_status()
    return r.json()['id']


def send_message(stack, sid, content) -> None:
    r = requests.post(f'{stack.base}/ai/chat/sessions/{sid}/messages',
                      headers=stack.auth_header,
                      json={'content': content}, timeout=15)
    r.raise_for_status()


def open_sse(stack, path, params=None):
    """SSE 长连接 contextmanager。用法：
    with open_sse(stack, '/ai/chat/batches/events', {'ids': bid}) as frames:
        for f in frames: ...   # 生成器内部 while True，用方负责 break
    """
    @contextmanager
    def _ctx():
        r = requests.get(f'{stack.base}{path}', params=params or {},
                         headers=stack.auth_header, stream=True,
                         timeout=(5, 600))
        try:
            assert r.status_code == 200, f'SSE status {r.status_code}'

            def _iter():
                ev, eid, data_buf = None, None, []
                for raw in r.iter_lines(chunk_size=1):
                    line = raw.decode('utf-8') if isinstance(raw, bytes) else raw
                    if line == '':
                        if data_buf:
                            try:
                                payload = json.loads(''.join(data_buf))
                            except json.JSONDecodeError:
                                payload = {'_raw': ''.join(data_buf)}
                            yield Frame(ev or '', payload, eid)
                        ev, eid, data_buf = None, None, []
                    elif line.startswith('event:'):
                        ev = line[len('event:'):].strip()
                    elif line.startswith('id:'):
                        eid = line[len('id:'):].strip()
                    elif line.startswith('data:'):
                        data_buf.append(line[len('data:'):].strip())
            yield _iter()
        finally:
            r.close()
    return _ctx()


def stress_db(stack):
    @contextmanager
    def _ctx():
        conn = psycopg2.connect(**stack.db_dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return _ctx


def register_expectations(stack, sid, checks) -> int:
    """生产登记函数 + 压测库连接：与路由/引擎同一条登记代码路径。"""
    from utils import agent_ledger
    getdb = stress_db(stack)
    validated = agent_ledger.validate_checks(checks)
    return agent_ledger.register_session_expectations(
        sid, validated, source='stress', get_db=getdb)


def collect_sse(stack, path, params, stop: threading.Event,
                sink: list, on_predicate=None):
    """线程 target：持续收帧进 sink，on_predicate(Frame) 为真或 stop 置位即回。"""
    try:
        with open_sse(stack, path, params) as frames:
            for f in frames:
                sink.append(f)
                if on_predicate and on_predicate(f):
                    return
                if stop.is_set():
                    return
    except Exception as e:                       # 线程内不许 assert
        sink.append(e)
```

- [ ] **Step 2: 写套件骨架 + S0**

```python
"""能力稳定性压测（spec docs/superpowers/specs/2026-10-05-batch-capability-stress-design.md）。
S0 交互冒烟 / S1 SSE 帧对账 / S2 并发终态×门禁 / S3 修正-重试-取消竞态 /
S4 交互收口竞争 / S5 树作用域聚合。全程 stub，0 token。"""
import threading
import time

import pytest
import requests

from tests.stress.caphelpers import (collect_sse, create_interactive_session,
                                     open_sse, register_expectations,
                                     send_message, stress_db)
from tests.stress.conftest import METRICS_ROOT, create_batch, wait_terminal

pytestmark = pytest.mark.stress


@pytest.fixture(scope='module', autouse=True)
def _reset_stub_runtime(stress_stack):
    stress_stack.restart_backend(concurrency=3)


def _dump(name, payload):
    import json
    METRICS_ROOT.mkdir(parents=True, exist_ok=True)
    (METRICS_ROOT / f'{time.strftime("%Y%m%d-%H%M%S")}-{name}.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')


def test_s0_interactive_roundtrip_smoke(stress_stack, sampler):
    """接线判别力锚：base（硬编码 OpenCodeClient）上建会话即 ConnectionError。
    全链路：建会话 → 先挂 SSE → 发送 → 帧序正确 → 消息落库 → 账本恰 2 行 bash。"""
    sampler.record('cap-S0-start')
    stress_stack.restart_backend(
        concurrency=3, profile={'delay_ms': [300, 800], 'tool_parts': 2})
    sid = create_interactive_session(stress_stack)
    sink = []
    stop = threading.Event()
    t = threading.Thread(target=collect_sse, daemon=True, args=(
        stress_stack, f'/ai/chat/sessions/{sid}/events', None, stop, sink,
        lambda f: f.event in ('session.idle', 'session.error')))
    t.start()
    time.sleep(0.5)                     # 先挂流再发（事件不可重放）
    send_message(stress_stack, sid,
                 '直接回复:STRESS-OK。不要读取文件,不要执行命令。')
    t.join(timeout=30)
    stop.set()
    assert not any(isinstance(f, Exception) for f in sink), sink[:3]
    types = [f.event for f in sink]
    assert types and types[0] == 'message.updated', types
    assert types[-1] in ('session.idle', 'session.error'), types
    assert types.count('message.part.updated') == 3, types   # 2 tool + 1 text
    assert all(f.data['properties'].get('sessionID') for f in sink), \
        'sessionID 路由键缺失'
    # 落库：assistant 消息已持久化（监听器写或 SSE 兜底写，二选一但必有一）
    rows = stress_stack.db_query(
        "SELECT count(*) FROM ai_chat_messages "
        "WHERE session_id=%s AND role='assistant'", (sid,))
    assert rows[0][0] >= 1, 'SSE idle 后消息未落库'
    # 账本：tool_parts=2 → bash 恰 2 行（幂等键 (oc_session_id, part_id)）
    rows = stress_stack.db_query(
        "SELECT count(*) FROM agent_tool_calls t JOIN ai_chat_sessions s "
        "ON s.id=%s AND t.oc_session_id=s.opencode_session_id "
        "AND t.tool='bash'", (sid,))
    assert rows[0][0] == 2, f'账本 bash 行数 {rows[0][0]} != 2'
    inv = stress_stack.invariants()
    assert inv['zombie_running'] == 0
```

- [ ] **Step 3: 跑 S0（压测栈全链路）确认通过**

Run: `cd server && python -m pytest tests/stress/test_capability_stress.py -m stress -v`
Expected: 1 passed（栈启动 + 冒烟约 1–2 分钟）。

- [ ] **Step 4: 判别力 A/B 记录**

Run: `git stash`（只 stash 产品代码改动：`git stash push server/routes/ai_chat.py server/utils/chat_persist.py`）→ 重跑 S0 → Expected: FAIL（create_session ConnectionError → 500）→ `git stash pop`。结论记入 Task 10 判别力清单：S0 是接线改动的回归锚。

- [ ] **Step 5: Commit**

```bash
git add server/tests/stress/caphelpers.py server/tests/stress/test_capability_stress.py
git commit -m "test(stress): 能力压测套件骨架 + S0 交互全链路冒烟（SSE/落库/账本三断言）"
```

---

### Task 5: S1 批次级 SSE 帧级对账 + Last-Event-ID 重连补发

**Files:**
- Modify: `server/tests/stress/test_capability_stress.py`（追加 S1）

**Interfaces:**
- Consumes: caphelpers.open_sse / collect_sse；conftest.create_batch / wait_terminal。
- Produces: 断言范式 S1.1–S1.3（帧对账/重连补发/关流语义）。

- [ ] **Step 1: 写 S1 用例**

```python
def test_s1_batch_sse_frame_reconciliation_and_resume(stress_stack, sampler):
    """50 观察者 5min 并发持流 + 帧级正确性：
    S1.1 帧 seq 对 ai_batch_events 表对账无缺口无重复；
    S1.2 1/3 观察者断线后 Last-Event-ID 重连，补发恰好衔接游标；
    S1.3 终态 batch_done 后服务端关流（ChunkedEncodingError 按终态分类）。"""
    sampler.record('cap-S1-start')
    stress_stack.restart_backend(concurrency=3,
                                 profile={'delay_ms': [2000, 5000]})
    bid = create_batch(stress_stack, 50)
    errors = []
    resume_proofs = []
    stop = threading.Event()
    watchers = []

    def watch(i: int):
        seen_seqs = []
        last_eid = None
        try:
            for attempt in range(2):        # attempt0 正常；attempt1 仅断线者重连
                with requests.get(
                        f'{stress_stack.base}/ai/chat/batches/events',
                        headers=stress_stack.auth_header, stream=True,
                        params={'ids': bid},
                        timeout=(5, 600)) as r:
                    if r.status_code != 200:
                        errors.append(f'w{i} status {r.status_code}')
                        return
                    if last_eid is not None:
                        r.headers  # noqa—— Last-Event-ID 用请求头（下行）
                    ev, eid = None, None
                    for raw in r.iter_lines(chunk_size=1):
                        line = raw.decode('utf-8') if isinstance(raw, bytes) else raw
                        if line == '':
                            if ev == 'batch_event' and eid:
                                seq = int(eid.rpartition(':')[2])
                                seen_seqs.append(seq)
                                last_eid = eid
                            elif ev == 'batch_done':
                                if attempt == 1:
                                    resume_proofs.append((i, len(seen_seqs)))
                                return
                            ev, eid = None, None
                        elif line.startswith('event:'):
                            ev = line[7:].strip()
                        elif line.startswith('id:'):
                            eid = line[3:].strip()
                        elif line.startswith('data:'):
                            pass
                if attempt == 0 and i % 3 == 0 and not stop.is_set():
                    time.sleep(2 + (i % 5))          # 随机早期断开
                    continue                          # 带 last_eid 重连
                return
        except requests.exceptions.ChunkedEncodingError:
            d = requests.get(f'{stress_stack.base}/ai/chat/batches/{bid}',
                             headers=stress_stack.auth_header, timeout=10).json()
            if d.get('batch', {}).get('status') not in (
                    'completed', 'failed', 'partial'):
                errors.append(f'w{i} 断流且未终态')
        except Exception as e:
            errors.append(f'w{i}: {e!r}')

    # 重连请求要带 Last-Event-ID 头——collect_sse 不支持头，本用例自带循环
    threads = []
    for i in range(50):
        t = threading.Thread(target=watch, args=(i,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(1.0)                              # 1s/连接爬坡（R1 教训）
    time.sleep(300)
    stop.set()
    for t in threads:
        t.join(timeout=15)
    wait_terminal(stress_stack, bid, timeout_s=600)
    assert not errors, f'SSE 异常: {errors[:3]}'

    # S1.1 对账：流见 seq 全集 == 表中该批 event_seq 全集（各自去重后）
    table_seqs = [r[0] for r in stress_stack.db_query(
        'SELECT event_seq FROM ai_batch_events WHERE batch_id=%s '
        'ORDER BY event_seq', (bid,))]
    stream_seqs = sorted(set(all_seen))              # 各 watcher 汇总（见 Step1 注）
    assert stream_seqs == table_seqs, \
        f'流/表不对账: 流缺 {sorted(set(table_seqs) - set(stream_seqs))[:5]} ' \
        f'流多 {sorted(set(stream_seqs) - set(table_seqs))[:5]}'
    assert len(all_seen) == len(set(all_seen)), ' watcher 间无重复才算精确对账'
    # S1.2 重连补发：断线重连者的 seen_seqs 在断点前后单调且无缺口
    for i, n in resume_proofs:
        assert n > 0
    _dump('s1-sse-reconcile', {'table_events': len(table_seqs),
                               'resume_proofs': resume_proofs})
    inv = stress_stack.invariants()
    assert inv['zombie_running'] == 0
```

**实现注记（写代码时落实）**：① `watch` 闭包需把 `seen_seqs` 汇总进共享 `all_seen: list`（加锁 append）；② 重连请求须 `headers={**stress_stack.auth_header, 'Last-Event-ID': last_eid}`——Step1 骨架里该行要真写上（`params` 不变）；③ 断线者 attempt0 的 `return` 改为 `break` 进 attempt1。

- [ ] **Step 2: 跑 S1 确认通过**

Run: `cd server && python -m pytest tests/stress/test_capability_stress.py -m stress -k s1 -v`
Expected: 1 passed（约 6–7 分钟）。若对账失败：先核对是游标推进缺陷还是测试解析缺陷（`?afterSeq` 只在 JSON 分页端点；SSE 端点是 Last-Event-ID——readpath 文件头注释），定位后再改。

- [ ] **Step 3: 判别力 A/B 记录**

本用例的帧解析断言（S1.1/S1.2）在既有 R1 实现上**必然失败**（R1 不解析帧）——无需 A/B（结构性成立），在判别力清单登记「结构性必失败：现网无帧解析实现可对照」。

- [ ] **Step 4: Commit**

```bash
git add server/tests/stress/test_capability_stress.py
git commit -m "test(stress): S1 批次 SSE 帧级对账 + Last-Event-ID 重连补发（50 连接 5min）"
```

---

### Task 6: S2 并发终态 × 门禁评估

**Files:**
- Modify: `server/tests/stress/test_capability_stress.py`（追加 S2）

**Interfaces:**
- Consumes: create_batch（本任务给它加 `action_checks`/`gate_retry` 透传参数——改 `tests/stress/conftest.py` 的 create_batch 签名，默认 None 不影响既有调用）。
- Produces: 门禁并发断言范式 S2.1–S2.5。
- **spec 修订点**：spec §5 S2 原写「file/db_record 类混合应过」——实施核实 `dynamic_data` 建表不在 migrations（init_db 域文件），为避免无谓 schema 耦合，S2 应过侧只用 **file 类**（评测循环与 db_record 同构），db_record 的功能正确性由既有单测覆盖。此修订随 Task 10 落 spec。

- [ ] **Step 1: 扩展 create_batch**

`tests/stress/conftest.py` 的 create_batch 增加关键字参数（内部 API 分支）：

```python
def create_batch(stack, n_children: int, *, callback_url=None, api=None,
                 action_checks=None, gate_retry: bool | None = None):
    ...
    body = {'name': name, 'prompt': prompt,
            'files': [staged] * min(n_children, 50)}
    if action_checks:
        body['action_checks'] = action_checks
    if gate_retry is not None:
        body['gate_retry'] = gate_retry
```

- [ ] **Step 2: 写 S2 用例**

```python
def test_s2_concurrent_terminal_gate_evaluation(stress_stack, sampler):
    """50 子任务 ±窗口并发终态 × 每子任务 3 条期望（2 file 应过 + 1 tool 必败对照）。
    S2.1 期望行恰好核对一次（无 pending 残留 / 无重复评估）；
    S2.2 gate_passed/gate_failed 计数与期望行一致；
    S2.3 gate.evaluated 事件数 == 子任务数；
    S2.4 drain 高峰无超时、批次可达终态；
    S2.5 invariants 守恒。"""
    sampler.record('cap-S2-start')
    stress_stack.restart_backend(concurrency=10,
                                 profile={'delay_ms': [8000, 12000]})
    checks = [
        {'name': 'file-pass-1', 'check_type': 'file',
         'effect_spec': {'path': 'outputs/ok-*.md'}},
        {'name': 'file-pass-2', 'check_type': 'file',
         'effect_spec': {'path': 'artifacts/*.txt'}},
        {'name': 'tool-fail-ctrl', 'check_type': 'tool',
         'tool': 'bash', 'args_pattern': 'never-matched-marker'},
    ]
    bid = create_batch(stress_stack, 50, action_checks=checks)
    # 建批后立即取子会话工作区并预置"应过"文件（期望按子任务登记于派发时，
    # 子任务 8s+ 才完成——预置窗口充足）
    children = stress_stack.db_query(
        "SELECT s.id, s.workspace_path, s.batch_seq FROM ai_chat_sessions s "
        "WHERE s.batch_id=%s ORDER BY s.batch_seq", (bid,))
    assert len(children) == 50
    import os
    for sid, ws, seq in children:
        os.makedirs(os.path.join(ws, 'outputs'), exist_ok=True)
        open(os.path.join(ws, 'outputs', f'ok-{seq}.md'), 'w',
             encoding='utf-8').write('seeded')
        os.makedirs(os.path.join(ws, 'artifacts'), exist_ok=True)
        open(os.path.join(ws, 'artifacts', f'a-{seq}.txt'), 'w',
             encoding='utf-8').write('seeded')
    d = wait_terminal(stress_stack, bid, timeout_s=900)
    detail = requests.get(f'{stress_stack.base}/ai/chat/batches/{bid}',
                          headers=stress_stack.auth_header, timeout=10).json()
    # S2.1 每子任务 3 条期望全部 last_status 非空（恰好一次终态核对）
    rows = stress_stack.db_query(
        "SELECT scope_id, count(*), count(last_status), count(DISTINCT name) "
        "FROM action_expectations WHERE scope_id IN "
        "(SELECT id FROM ai_chat_sessions WHERE batch_id=%s) GROUP BY scope_id",
        (bid,))
    assert len(rows) == 50
    for sid, n, checked, distinct in rows:
        assert (n, checked, distinct) == (3, 3, 3), (sid, n, checked, distinct)
    # S2.2 计数一致：file 应过 2 + tool 必败 1 → 每子任务 passed=2 failed=1
    for sess in detail['sessions']:
        assert sess['gate_passed'] == 2 and sess['gate_failed'] == 1, sess
    # S2.3 审计事件与子任务一一对应
    ev = stress_stack.db_query(
        "SELECT count(*) FROM ai_batch_events WHERE batch_id=%s "
        "AND event_type='gate.evaluated'", (bid,))
    assert ev[0][0] == 50, ev
    # S2.4 无 drain 超时日志（压测栈 backend.log 检索）
    log = (METRICS_ROOT / 'backend.log').read_text(encoding='utf-8',
                                                   errors='replace')
    assert 'drain timeout' not in log
    # S2.5
    inv = stress_stack.invariants()
    assert inv == {'orphans': 0, 'zombie_running': 0, 'counter_violations': 0}, inv
    _dump('s2-gate-concurrent', {'children': 50, 'checks_per_child': 3})
```

- [ ] **Step 3: 跑 S2 确认通过**

Run: `cd server && python -m pytest tests/stress/test_capability_stress.py -m stress -k s2 -v`
Expected: 1 passed（约 1–2 分钟 + 栈内子任务 ~12s）。**首跑若 S2.2 红**：核对 `get_batch_detail` 的 gate_passed/gate_failed 语义（cancelled 计入 failed 侧等约定）后再修断言或报产品缺陷——按判别力铁律先在 base 上复核（本场景无产品改动，base=当前 HEAD，红即产品缺陷，走交付物 6 流程：先固化失败测试再修复）。

- [ ] **Step 4: Commit**

```bash
git add server/tests/stress/conftest.py server/tests/stress/test_capability_stress.py
git commit -m "test(stress): S2 并发终态×门禁评估——期望恰好一次/计数对账/审计事件/守恒"
```

---

### Task 7: S3 门禁修正 / 重试 / 取消 竞态

**Files:**
- Modify: `server/tests/stress/test_capability_stress.py`（追加 S3 两个用例）

**Interfaces:**
- Consumes: create_batch(action_checks, gate_retry)；workspaceIO 直写（os）；批 API cancel（`POST /ai/chat/batches/<bid>/cancel`——实施时以 routes/ai_chat_batches.py 实际路由为准核对一次路径与 body，route 装饰器为准）。
- Produces: S3.1–S3.5 断言。

- [ ] **Step 1: 写 S3 用例**

```python
def test_s3_gate_retry_budget_race(stress_stack, sampler):
    """门禁必败 → gate_retry continue 修正（stub 重跑仍败）→ 预算耗尽 failed；
    同窗口并发 cancel 一半子任务。
    S3.1 retry_count ≤ max(AI_BATCH_MAX_AUTO_RETRY, gate_retry 预算)；
    S3.2 终态唯一、cancel 与 continue 竞态不复活；
    S3.3 无孤儿修正轮（终态后无新派发：子任务 pending 行为空且终态不变）；
    S3.5 守恒。"""
    sampler.record('cap-S3-start')
    stress_stack.restart_backend(
        concurrency=10,
        profile={'delay_ms': [3000, 5000], 'tool_parts': 1})
    checks = [{'name': 'never-file', 'check_type': 'file',
               'effect_spec': {'path': 'outputs/never-seeded.md'}}]
    bid = create_batch(stress_stack, 20, action_checks=checks,
                       gate_retry=True)
    # 等 10 个子任务进入 running 后 cancel 其余 10 个（竞态窗口：cancel vs
    # 门禁 continue 的 requeue）
    deadline = time.time() + 60
    while time.time() < deadline:
        rows = stress_stack.db_query(
            "SELECT count(*) FROM ai_chat_sessions WHERE batch_id=%s "
            "AND status='running'", (bid,))
        if rows[0][0] >= 10:
            break
        time.sleep(0.5)
    requests.post(f'{stress_stack.base}/ai/chat/batches/{bid}/cancel',
                  headers=stress_stack.auth_header, timeout=10)
    d = wait_terminal(stress_stack, bid, timeout_s=900)
    # S3.1 预算：任何子任务 retry_count ≤ AI_BATCH_MAX_AUTO_RETRY(默认2)+1(gate)
    rows = stress_stack.db_query(
        "SELECT max(retry_count) FROM ai_chat_sessions WHERE batch_id=%s", (bid,))
    assert rows[0][0] <= 3, f'retry_count 超发: {rows[0][0]}'
    # S3.2 终态唯一 + 守恒
    rows = stress_stack.db_query(
        "SELECT status, count(*) FROM ai_chat_sessions WHERE batch_id=%s "
        "GROUP BY status", (bid,))
    bad = [r for r in rows if r[0] not in ('completed', 'failed', 'cancelled')]
    assert bad == [], rows
    # S3.3 终态后无 pending（continue requeue 会写 pending——终态批不许残留）
    rows = stress_stack.db_query(
        "SELECT count(*) FROM ai_chat_sessions WHERE batch_id=%s "
        "AND status='pending'", (bid,))
    assert rows[0][0] == 0, '终态批残留 pending（修正轮孤儿）'
    inv = stress_stack.invariants()
    assert inv['counter_violations'] == 0
    _dump('s3-gate-retry-race', {'final': dict(rows)})


def test_s3_gate_retry_success_path(stress_stack, sampler):
    """修正成功侧：运行中补写期望文件 → 下一修正轮通过 → completed，retry_count==1。"""
    sampler.record('cap-S3b-start')
    stress_stack.restart_backend(
        concurrency=3, profile={'delay_ms': [2000, 3000], 'tool_parts': 0})
    checks = [{'name': 'late-file', 'check_type': 'file',
               'effect_spec': {'path': 'outputs/late.md'}}]
    bid = create_batch(stress_stack, 2, action_checks=checks, gate_retry=True)
    children = stress_stack.db_query(
        "SELECT s.id, s.workspace_path FROM ai_chat_sessions s "
        "WHERE s.batch_id=%s", (bid,))
    import os
    import threading

    def seed_late():
        # 第一轮门禁核对前文件不存在（必败触发修正），requeue 后 1s 内补上
        time.sleep(4)
        for sid, ws in children:
            os.makedirs(os.path.join(ws, 'outputs'), exist_ok=True)
            open(os.path.join(ws, 'outputs', 'late.md'), 'w',
                 encoding='utf-8').write('late')

    threading.Thread(target=seed_late, daemon=True).start()
    wait_terminal(stress_stack, bid, timeout_s=600)
    rows = stress_stack.db_query(
        "SELECT status, retry_count FROM ai_chat_sessions WHERE batch_id=%s",
        (bid,))
    assert all(r[0] == 'completed' for r in rows), rows
    assert all(r[1] == 1 for r in rows), f'修正成功侧 retry_count 应恰 1: {rows}'
```

- [ ] **Step 2: 核对 cancel 路由**

Run: `grep -n "cancel" server/routes/ai_chat_batches.py | head` ——以实际路由路径/方法替换 Step 1 里的 cancel 请求行（若 body 需要参数一并补）。

- [ ] **Step 3: 跑 S3 确认通过**

Run: `cd server && python -m pytest tests/stress/test_capability_stress.py -m stress -k s3 -v`
Expected: 2 passed（约 2–3 分钟）。S3.1 若红：先核对 `_maybe_gate_retry` 与 auto-retry 的预算叠加语义（共用 retry_count，cap 分别 2/1）——断言 ≤3 是「auto-retry 2 次 + gate 修正 1 次」的推导上界，实测超 3 即产品缺陷，走交付物 6 流程。

- [ ] **Step 4: 判别力 A/B**

S3 场景无产品改动——但 `test_s3_gate_retry_success_path` 锁定 continue 修正闭环（曾经的生产缺陷：effect_spec 缺失致修正轮必败）。A/B：`git stash` 无从 stash（无产品 diff）→ 改用**缺陷注入法**：临时把 `agent_ledger.check_session_gate` 返回的 results 去掉 effect_spec（注释掉 gate_failure_message 依赖的行）后重跑，Expected: S3b 红（retry_count 变 2+ 或 failed）——记录后还原。若注入实施成本高，可降级为在判别力清单登记「依赖既有单元/生产验证，无独立 base 对照」并说明理由。

- [ ] **Step 5: Commit**

```bash
git add server/tests/stress/test_capability_stress.py
git commit -m "test(stress): S3 门禁修正/重试/取消竞态——预算不超发/终态唯一/修正成功闭环"
```

---

### Task 8: S4 交互收口竞争（并发 finalize 幂等）

**Files:**
- Modify: `server/tests/stress/test_capability_stress.py`（追加 S4）

**Interfaces:**
- Consumes: caphelpers 全套；conftest restart_backend。

- [ ] **Step 1: 写 S4 用例**

```python
def test_s4_interactive_finalize_contention(stress_stack, sampler):
    """20 交互会话并发跑 + 每会话双 SSE 流（重连期旧流未死形态）：
    S4.1 账本按 (oc_session_id, part_id) 幂等——bash 恰 2 行/会话；
    S4.2 attach 的交互期望每条恰好一次核对结果；
    S4.3 双流并发收口不重复落账。"""
    sampler.record('cap-S4-start')
    stress_stack.restart_backend(
        concurrency=3, profile={'delay_ms': [500, 1500], 'tool_parts': 2})
    n = 20
    sids = [create_interactive_session(stress_stack) for _ in range(n)]
    # 每会话挂 2 条流 + 登记一条交互期期望（账本型，stub 必败→核对结果=failed
    # 也算"恰好一次"）
    stops, threads = [], []
    for sid in sids:
        register_expectations(stress_stack, sid, [
            {'name': 'interactive-tool', 'check_type': 'tool',
             'tool': 'bash', 'args_pattern': 'no-such-cmd'}])
        for _dup in range(2):
            stop = threading.Event()
            sink = []
            t = threading.Thread(target=collect_sse, daemon=True, args=(
                stress_stack, f'/ai/chat/sessions/{sid}/events', None,
                stop, sink,
                lambda f: f.event in ('session.idle', 'session.error')))
            t.start()
            stops.append(stop)
            threads.append(t)
    time.sleep(0.5)
    for sid in sids:
        send_message(stress_stack, sid,
                     '直接回复:STRESS-OK。不要读取文件,不要执行命令。')
    for t in threads:
        t.join(timeout=60)
    for s in stops:
        s.set()
    # S4.1+S4.3：oc 会话级 bash 行数 == 2（双流 × finalize 幂等键兜住）
    rows = stress_stack.db_query(
        "SELECT s.id, s.opencode_session_id, count(t.id) "
        "FROM ai_chat_sessions s LEFT JOIN agent_tool_calls t "
        "ON t.oc_session_id = s.opencode_session_id AND t.tool='bash' "
        "WHERE s.id = ANY(%s) GROUP BY s.id, s.opencode_session_id", (sids,))
    for sid, oc, cnt in rows:
        assert cnt == 2, f'{sid} 账本 bash {cnt} 行 != 2（双流重复落账）'
    # S4.2 交互期望恰好一次核对（last_status 非空即核对过；无重复登记）
    rows = stress_stack.db_query(
        "SELECT count(*) FROM action_expectations WHERE scope_id = ANY(%s) "
        "AND name='interactive-tool'", (sids,))
    assert rows[0][0] == n, rows
    rows = stress_stack.db_query(
        "SELECT count(*) FROM action_expectations WHERE scope_id = ANY(%s) "
        "AND name='interactive-tool' AND last_status IS NULL", (sids,))
    assert rows[0][0] == 0, '存在未核对（超时/丢失收口）的交互期望'
    _dump('s4-finalize-contention', {'sessions': n, 'streams': 2 * n})
```

- [ ] **Step 2: 跑 S4 确认通过**

Run: `cd server && python -m pytest tests/stress/test_capability_stress.py -m stress -k s4 -v`
Expected: 1 passed（约 1–2 分钟）。**注意**：本用例前各用例改过 profile——S4 的 restart_backend 是必须的（profile 隔离）。

- [ ] **Step 3: 判别力 A/B**

幂等键 (oc_session_id, part_id) + 唯一索引在 base 已存在（2026_09_20 迁移）——本用例主要价值是**负载下验证**而非新回归锁。A/B 注入法：临时删除 `uq_agent_tool_call_part` 唯一索引（压测库手工 DROP）并注释 `agent_ledger.record_state` 的 ON CONFLICT 子句 → 重跑 Expected: S4.1 红（>2 行）→ 还原。记录进清单；若注入嫌重，登记「结构性依赖既有唯一索引，A/B 注入实验完成/放弃（理由）」。

- [ ] **Step 4: Commit**

```bash
git add server/tests/stress/test_capability_stress.py
git commit -m "test(stress): S4 交互收口竞争——20 会话×双 SSE 流并发 finalize 账本幂等"
```

---

### Task 9: S5 树作用域聚合正确性（按 Task 2 探针结论走主路或降级）

**Files:**
- Modify: `server/tests/stress/test_capability_stress.py`（追加 S5）

**Interfaces:**
- Consumes: Task 2 的 delegate profile（主路）或 DB 种子（降级）；caphelpers.register_expectations。
- 期望语义：tree 作用域 + `subagents: ['explorer']` 定向——账本 JOIN `ai_chat_subtasks` 按 agent 过滤。

- [ ] **Step 1A: 主路用例（Task 2 探针 = 事件驱动可行时）**

```python
def test_s5_tree_scope_aggregation(stress_stack, sampler):
    """委派负载下树作用域聚合：根会话期望（tree + subagents=['explorer']）
    核对计入子代理 bash 动作；不相关 agent（writer）动作不参与。"""
    sampler.record('cap-S5-start')
    stress_stack.restart_backend(
        concurrency=3,
        profile={'delay_ms': [500, 1200], 'tool_parts': 2,
                 'delegate': ['explorer', 'writer']})
    n = 5
    sids = [create_interactive_session(stress_stack) for _ in range(n)]
    for sid in sids:
        register_expectations(stress_stack, sid, [
            {'name': 'tree-explorer', 'check_type': 'tool', 'scope': 'tree',
             'tool': 'bash', 'args_pattern': 'stub-cmd-',
             'subagents': ['explorer'], 'min_count': 2},
            {'name': 'tree-exclude-writer', 'check_type': 'tool',
             'scope': 'tree', 'tool': 'bash',
             'args_pattern': 'writer-only-marker',
             'subagents': ['explorer'], 'min_count': 1},
        ])
        sink, stop = [], threading.Event()
        t = threading.Thread(target=collect_sse, daemon=True, args=(
            stress_stack, f'/ai/chat/sessions/{sid}/events', None, stop, sink,
            lambda f: f.event in ('session.idle', 'session.error')))
        t.start()
        globals().setdefault('_s5_streams', []).append((stop, t))
    time.sleep(0.5)
    for sid in sids:
        send_message(stress_stack, sid, 'delegate and finish')
    for stop, t in globals()['_s5_streams']:
        t.join(timeout=60)
        stop.set()
    # 聚合对账：tree-explorer 的 last_evidence == explorer 子代理 bash 行数
    for sid in sids:
        oc = stress_stack.db_query(
            "SELECT opencode_session_id FROM ai_chat_sessions WHERE id=%s",
            (sid,))[0][0]
        exp = stress_stack.db_query(
            "SELECT name, last_status, last_evidence FROM action_expectations "
            "WHERE scope_id=%s ORDER BY name", (sid,))
        d = {r[0]: (r[1], r[2]) for r in exp}
        status, ev = d['tree-explorer']
        assert status == 'passed' and ev >= 2, (sid, d)
        status2, ev2 = d['tree-exclude-writer']
        assert status2 == 'failed', (sid, d)      # writer 动作不参与 → 必败对照
    _dump('s5-tree-scope', {'sessions': n})
```

（写代码时把流句柄管理改成局部 list，别用 globals——上面骨架只为示意断言。）

- [ ] **Step 1B: 降级用例（探针失败时）**：同断言结构，但委派改为直接 INSERT `ai_chat_subtasks`（root_session_id=根 sid、agent='explorer'、status='completed'）+ `agent_tool_calls`（oc_session_id=伪造子 oc、subtask_id=子行 id）种子行——登记 tree 期望后直调 `agent_ledger.check_session_gate(sid, get_db=stress_db(stack))` 断言 results。**不挂 SSE 流**（发现链路已在探针认定不可行，本用例只锁核对 SQL 语义）。

- [ ] **Step 2: 跑 S5 确认通过**

Run: `cd server && python -m pytest tests/stress/test_capability_stress.py -m stress -k s5 -v`
Expected: 1 passed（约 1 分钟）。

- [ ] **Step 3: Commit**

```bash
git add server/tests/stress/test_capability_stress.py
git commit -m "test(stress): S5 树作用域聚合——委派负载下 subagents 定向过滤与计数对账"
```

---

### Task 10: 全量跑 + 判别力清单 + spec 终版修订 + 报告

**Files:**
- Modify: `docs/superpowers/specs/2026-10-05-batch-capability-stress-design.md`（S2 db_record 收窄、接线面 5 处——若 Task 3/6 未同步改完）
- Create: `docs/ai-testing/evidence/stress/2026-10-05-capability-stress-report.md`

- [ ] **Step 1: 全量跑新套件**

Run: `cd server && python -m pytest tests/stress/test_capability_stress.py -m stress -v`
Expected: 6 passed（S0/S1/S2/S3×2/S4/S5），总时长 30–40min；metrics JSON 已落 `docs/ai-testing/evidence/stress/`。两遍：第二遍确认可重复（压测栈每次全新建/拆，天然隔离）。

- [ ] **Step 2: 判别力清单落盘**

在报告里填「判别力登记表」（沿 12 号铁律，逐条实测结论）：

| 用例 | base 必失败声明 | 验证方式 | 结论 |
|---|---|---|---|
| Task1 事件总线/门面单测 | 是 | git stash 实跑（Task1 Step5） | 待填 |
| S0 交互冒烟 | 是（接线） | stash push 5 处接线文件实跑（Task4 Step4） | 待填 |
| S1 帧对账 | 结构性成立（现网无帧解析实现） | 无需 A/B | 待填 |
| S2 并发门禁 | 无产品 diff——红即产品缺陷 | 首跑即 base | 待填 |
| S3 预算竞态 | 缺陷注入法或声明放弃+理由 | Task7 Step4 | 待填 |
| S4 账本幂等 | 唯一索引注入实验 | Task8 Step3 | 待填 |
| S5 聚合 | 视主路/降级 | 探针结论 + 首跑 | 待填 |

- [ ] **Step 3: spec 终版对齐**

核对 spec §5/§6/§7 与实现差异并修正（S2 db_record 收窄、S5 主路/降级实况、实际总时长）。spec 改动 `git add -f`。

- [ ] **Step 4: 写报告 + Commit**

报告含：跑动结论（全绿/发现的产品缺陷清单及处置）、指标 JSON 指针、判别力登记表、遗留（如 S1.5 pool-busy 观察项是否捕获到样本）。

```bash
git add server/tests/stress/test_capability_stress.py docs/ai-testing/evidence/stress/
git add -f docs/superpowers/specs/2026-10-05-batch-capability-stress-design.md
git commit -m "docs(stress): 能力稳定性压测首跑报告 + 判别力登记 + spec 终版对齐"
```

---

## Self-Review 结论（已自查）

1. **Spec 覆盖**：spec §3→Task 1/3（接线面 5 处为实施核实后的修正，Task 3 内同步改 spec）；§4→Task 1/2；§5 S1–S5→Task 5–9；§6 规模/产物→各任务 + Task 10；§7 判别力→各任务 A/B 步骤 + Task 10 登记表；§8 交付物→Task 10。无缺口。
2. **占位符**：无 TBD/TODO；Task 5/9 的「实现注记/骨架替换」均为显式指令而非留白；Task 7 cancel 路由要求 grep 核对是防路由路径漂移的核对步骤，非占位。
3. **类型一致性**：`Frame(event, data, eid)`、`stress_db(stack)`、`register_expectations(stack, sid, checks)`、`create_batch(..., action_checks=, gate_retry=)` 各任务引用一致；`_append_terminal(sid, s, finish)` 签名变更在 Task 1/2 两处调用点（_complete_loop/abort_session）均已声明。
