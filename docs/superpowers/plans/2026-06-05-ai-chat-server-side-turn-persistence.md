# AI Chat 服务端独立持久化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让助手回复（含工具调用）由后端独立于浏览器连接持久化，使切换会话/关闭抽屉时不再丢失中间态、回合完成后可在历史中找回。

**Architecture:** 新增 `server/utils/chat_persist.py`：把事件累积逻辑抽成纯函数（`new_state`/`apply_event`/`build_content`），加一个按会话的后台守护线程（`ensure_listener`）订阅 OpenCode 事件并在 `session.idle` 幂等 upsert 助手消息。SSE 代理改用同一套 helper 并改为幂等持久化，作为安全网；两者以 OpenCode 消息 id 为行 id 收敛到同一行，不产生重复。

**Tech Stack:** Python Flask + psycopg2 + threading；pytest（DB 用 mock cursor，事件源用注入的假迭代器）。

---

## File Structure

- `server/utils/chat_persist.py` — 新单元：纯累积 helper、`persist_turn` 幂等 upsert、`_run_listener` 循环、`ensure_listener`/`stop_listener` 线程管理。
- `server/tests/test_chat_persist.py` — 单测（纯函数 + persist + 循环 + 去重）。
- `server/utils/opencode_client.py` — `subscribe_events` 增加可选 `read_timeout` 参数（监听线程用它实现不活跃超时）。
- `server/routes/ai_chat.py` — `sse_events` 改用共享 helper + 幂等持久化；`send_message`/`run_session_command` 调 `ensure_listener`；`delete_session` 调 `stop_listener`。

---

## Task 1: chat_persist 纯累积 helper

**Files:**
- Create: `server/utils/chat_persist.py`
- Test: `server/tests/test_chat_persist.py`

- [ ] **Step 1: Write the failing test** — create `server/tests/test_chat_persist.py`:

```python
"""Tests for utils.chat_persist."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _ev(etype, props):
    return {'event': etype, 'data': {'properties': props}}


def test_apply_event_accumulates_text_tool_and_detects_idle():
    from utils.chat_persist import new_state, apply_event, build_content
    s = new_state()
    # assistant message announced -> records turn_msg_id
    assert apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc') is None
    assert s['turn_msg_id'] == 'm1'
    # text part
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'text', 'text': 'hi', 'sessionID': 'oc'}}), 'oc')
    # tool part
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p2', 'messageID': 'm1', 'type': 'tool', 'tool': 'query',
                  'state': {'status': 'completed', 'title': 'q', 'output': 'res'}, 'sessionID': 'oc'}}), 'oc')
    # idle ends the turn
    assert apply_event(s, _ev('session.idle', {'sessionID': 'oc'}), 'oc') == 'idle'
    content = build_content(s)
    assert content[0] == {'type': 'text', 'text': 'hi'}
    assert content[1]['type'] == 'tool_use'
    assert content[1]['name'] == 'query'
    assert content[1]['result'] == 'res'


def test_apply_event_ignores_other_session():
    from utils.chat_persist import new_state, apply_event
    s = new_state()
    apply_event(s, _ev('message.updated',
        {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'OTHER'}}), 'oc')
    assert s['turn_msg_id'] is None


def test_build_content_drops_empty_text():
    from utils.chat_persist import new_state, apply_event, build_content
    s = new_state()
    apply_event(s, _ev('message.updated', {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}), 'oc')
    apply_event(s, _ev('message.part.updated',
        {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'text', 'text': '   ', 'sessionID': 'oc'}}), 'oc')
    assert build_content(s) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.chat_persist'`

- [ ] **Step 3: Write minimal implementation** — create `server/utils/chat_persist.py`:

```python
"""Server-side persistence of AI chat assistant turns, decoupled from the
browser SSE connection.

A per-session daemon thread subscribes to OpenCode's event stream and persists
the assistant message on `session.idle`, so switching sessions mid-stream no
longer loses tool calls / partial output. The accumulation helpers are also
used by the browser SSE proxy so both share one tested implementation."""
import json
import secrets
import threading

from db import get_db
from utils.opencode_client import OpenCodeClient
from config import OPENCODE_BASE_URL

INACTIVITY_TIMEOUT = 30 * 60  # seconds; listener exits after this much silence

_listeners = {}          # sid -> threading.Thread
_lock = threading.Lock()


def _event_session_id(props):
    """OpenCode puts the session id in different nested spots per event type."""
    if not isinstance(props, dict):
        return None
    if props.get('sessionID'):
        return props['sessionID']
    for k in ('part', 'info'):
        v = props.get(k)
        if isinstance(v, dict) and v.get('sessionID'):
            return v['sessionID']
    return None


def new_state():
    """Fresh per-turn accumulator."""
    return {'assistant_msg_ids': set(), 'parts_by_id': {}, 'part_order': [], 'turn_msg_id': None}


def apply_event(state, evt, opencode_session_id):
    """Consume one subscribe_events() item ({'event','data'}). Accumulate
    assistant text/tool parts into `state`. Return 'idle' on session.idle.
    Events for other sessions are ignored."""
    etype = evt.get('event', '')
    props = (evt.get('data') or {}).get('properties') or {}
    ev_sid = _event_session_id(props)
    if ev_sid and ev_sid != opencode_session_id:
        return None
    if etype == 'message.updated':
        info = props.get('info') or {}
        if info.get('role') == 'assistant' and info.get('id'):
            state['assistant_msg_ids'].add(info['id'])
            if state['turn_msg_id'] is None:
                state['turn_msg_id'] = info['id']
    elif etype == 'message.part.updated':
        part = props.get('part') or {}
        pid = part.get('id')
        if pid and part.get('messageID') in state['assistant_msg_ids']:
            ptype = part.get('type')
            if ptype == 'text':
                if pid not in state['parts_by_id']:
                    state['part_order'].append(pid)
                state['parts_by_id'][pid] = {'type': 'text', 'text': part.get('text', '')}
            elif ptype == 'tool':
                if pid not in state['parts_by_id']:
                    state['part_order'].append(pid)
                st = part.get('state') or {}
                state['parts_by_id'][pid] = {
                    'type': 'tool_use',
                    'name': part.get('tool') or 'tool',
                    'title': st.get('title'),
                    'status': st.get('status'),
                    'input': st.get('input'),
                    'result': st.get('output') if st.get('output') is not None else st.get('result'),
                }
    elif etype == 'session.idle':
        return 'idle'
    return None


def build_content(state):
    """Build the persisted assistant content (arrival order). Empty text parts
    dropped; tool_use parts kept so rendered results survive a reload."""
    content = []
    for pid in state['part_order']:
        p = state['parts_by_id'].get(pid)
        if not p:
            continue
        if p['type'] == 'text':
            if (p.get('text') or '').strip():
                content.append({'type': 'text', 'text': p['text']})
        elif p['type'] == 'tool_use':
            content.append(p)
    return content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add server/utils/chat_persist.py server/tests/test_chat_persist.py
git commit -m "feat(ai-chat): chat_persist accumulation helpers (new_state/apply_event/build_content)"
```

---

## Task 2: 幂等持久化 `persist_turn`

**Files:**
- Modify: `server/utils/chat_persist.py`
- Test: `server/tests/test_chat_persist.py`

- [ ] **Step 1: Write the failing test** — append to `server/tests/test_chat_persist.py`:

```python
import contextlib


class _FakeCur:
    def __init__(self, sink): self._sink = sink
    def execute(self, sql, params=None): self._sink.append((sql, params))


def _fake_db(sink):
    @contextlib.contextmanager
    def _cm():
        class _Conn:
            def cursor(self): return _FakeCur(sink)
        yield _Conn()
    return _cm


def test_persist_turn_upserts_with_turn_msg_id(monkeypatch):
    from utils import chat_persist
    sink = []
    monkeypatch.setattr(chat_persist, 'get_db', _fake_db(sink))
    state = chat_persist.new_state()
    state['turn_msg_id'] = 'msg_A'
    state['part_order'] = ['p1']
    state['parts_by_id'] = {'p1': {'type': 'text', 'text': 'hi'}}
    chat_persist.persist_turn('sess1', state)
    assert len(sink) == 1
    sql, params = sink[0]
    assert 'ON CONFLICT (id) DO UPDATE' in sql
    assert params[0] == 'msg_A'      # deterministic row id = turn message id
    assert params[1] == 'sess1'


def test_persist_turn_noop_on_empty_content(monkeypatch):
    from utils import chat_persist
    sink = []
    monkeypatch.setattr(chat_persist, 'get_db', _fake_db(sink))
    chat_persist.persist_turn('sess1', chat_persist.new_state())  # no parts
    assert sink == []


def test_persist_turn_falls_back_to_generated_id(monkeypatch):
    from utils import chat_persist
    sink = []
    monkeypatch.setattr(chat_persist, 'get_db', _fake_db(sink))
    state = chat_persist.new_state()  # turn_msg_id stays None
    state['part_order'] = ['p1']
    state['parts_by_id'] = {'p1': {'type': 'text', 'text': 'hi'}}
    chat_persist.persist_turn('sess1', state)
    assert sink[0][1][0].startswith('msg_')   # generated fallback id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py -k persist_turn -v`
Expected: FAIL with `AttributeError: module 'utils.chat_persist' has no attribute 'persist_turn'`

- [ ] **Step 3: Write minimal implementation** — add to `server/utils/chat_persist.py` (after `build_content`):

```python
def persist_turn(session_id, state):
    """Idempotent upsert of the accumulated assistant message. No-op if the
    content is empty. Keyed on the turn's OpenCode message id so the browser
    SSE proxy and the background listener converge on the same row (no dupes)."""
    content = build_content(state)
    if not content:
        return
    row_id = state.get('turn_msg_id') or ('msg_' + secrets.token_hex(6))
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO ai_chat_messages (id, session_id, role, content) "
                "VALUES (%s, %s, 'assistant', %s) "
                "ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content",
                (row_id, session_id, json.dumps(content)),
            )
    except Exception:
        pass  # don't break the listener/stream on a DB hiccup
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add server/utils/chat_persist.py server/tests/test_chat_persist.py
git commit -m "feat(ai-chat): persist_turn idempotent upsert keyed on turn message id"
```

---

## Task 3: 监听循环 + 线程管理 + subscribe_events 读超时

**Files:**
- Modify: `server/utils/opencode_client.py` (`subscribe_events`)
- Modify: `server/utils/chat_persist.py` (`_run_listener`, `_listener_thread`, `ensure_listener`, `stop_listener`)
- Test: `server/tests/test_chat_persist.py`

- [ ] **Step 1: Write the failing test** — append to `server/tests/test_chat_persist.py`:

```python
import threading as _threading
import time as _time


def test_run_listener_persists_each_idle(monkeypatch):
    from utils import chat_persist
    saved = []
    monkeypatch.setattr(chat_persist, 'persist_turn',
                        lambda sid, state: saved.append((sid, chat_persist.build_content(state))))
    events = [
        _ev('message.updated', {'info': {'role': 'assistant', 'id': 'm1', 'sessionID': 'oc'}}),
        _ev('message.part.updated', {'part': {'id': 'p1', 'messageID': 'm1', 'type': 'text', 'text': 'one', 'sessionID': 'oc'}}),
        _ev('session.idle', {'sessionID': 'oc'}),
        _ev('message.updated', {'info': {'role': 'assistant', 'id': 'm2', 'sessionID': 'oc'}}),
        _ev('message.part.updated', {'part': {'id': 'p2', 'messageID': 'm2', 'type': 'text', 'text': 'two', 'sessionID': 'oc'}}),
        _ev('session.idle', {'sessionID': 'oc'}),
    ]
    chat_persist._run_listener('sess1', 'oc', iter(events))
    assert [c for _, c in saved] == [[{'type': 'text', 'text': 'one'}], [{'type': 'text', 'text': 'two'}]]


def test_ensure_listener_dedups_per_session(monkeypatch):
    from utils import chat_persist
    started = []
    block = _threading.Event()

    def fake_target(sid, oc, d):
        started.append(sid)
        block.wait(2)

    monkeypatch.setattr(chat_persist, '_listener_thread', fake_target)
    try:
        chat_persist.ensure_listener('s1', 'oc1', '/ws')
        chat_persist.ensure_listener('s1', 'oc1', '/ws')  # already alive -> no second thread
        _time.sleep(0.1)
        assert started.count('s1') == 1
    finally:
        block.set()
        _time.sleep(0.05)
        chat_persist._listeners.pop('s1', None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py -k "run_listener or ensure_listener" -v`
Expected: FAIL with `AttributeError: module 'utils.chat_persist' has no attribute '_run_listener'`

- [ ] **Step 3a: Add `read_timeout` to subscribe_events** — in `server/utils/opencode_client.py`, change the signature and the `requests.get` timeout:

Change:
```python
    def subscribe_events(self, directory: str = "") -> Iterator[dict]:
```
to:
```python
    def subscribe_events(self, directory: str = "", read_timeout=None) -> Iterator[dict]:
```
and change:
```python
        with requests.get(
            self._url("/event"),
            params=params,
            stream=True,
            timeout=None,
            headers={"Accept": "text/event-stream"},
        ) as resp:
```
to:
```python
        with requests.get(
            self._url("/event"),
            params=params,
            stream=True,
            timeout=read_timeout,
            headers={"Accept": "text/event-stream"},
        ) as resp:
```
(The browser SSE proxy calls it without `read_timeout`, so it keeps the previous `timeout=None` behavior.)

- [ ] **Step 3b: Add the loop + thread management** — add to `server/utils/chat_persist.py` (after `persist_turn`):

```python
def _run_listener(sid, opencode_session_id, event_source):
    """Consume events, persisting the assistant message on each session.idle.
    Returns when the source is exhausted/raises (inactivity read-timeout or
    stream end). Pure loop — `event_source` is injectable for tests."""
    state = new_state()
    for evt in event_source:
        if apply_event(state, evt, opencode_session_id) == 'idle':
            persist_turn(sid, state)
            state = new_state()


def _listener_thread(sid, opencode_session_id, directory):
    """Thread target: subscribe to OpenCode events for this session's workspace
    and run the persist loop. Exits on inactivity read-timeout or any error;
    removes itself from the registry so a later turn can start a fresh one."""
    try:
        source = OpenCodeClient(OPENCODE_BASE_URL).subscribe_events(
            directory=directory, read_timeout=INACTIVITY_TIMEOUT,
        )
        _run_listener(sid, opencode_session_id, source)
    except Exception:
        pass
    finally:
        with _lock:
            _listeners.pop(sid, None)


def ensure_listener(sid, opencode_session_id, directory):
    """Start a background persistence listener for `sid` if none is running.
    Called when a turn begins, so persistence happens even with no browser
    connected."""
    with _lock:
        existing = _listeners.get(sid)
        if existing and existing.is_alive():
            return
        t = threading.Thread(
            target=_listener_thread, args=(sid, opencode_session_id, directory), daemon=True,
        )
        _listeners[sid] = t
        t.start()


def stop_listener(sid):
    """Drop a session's listener from the registry (on session delete). The
    daemon thread itself exits once its OpenCode stream ends/errors."""
    with _lock:
        _listeners.pop(sid, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_chat_persist.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add server/utils/chat_persist.py server/utils/opencode_client.py server/tests/test_chat_persist.py
git commit -m "feat(ai-chat): per-session persistence listener + subscribe_events read_timeout"
```

---

## Task 4: 接线到路由（代理共用 helper + 接线 ensure/stop）

**Files:**
- Modify: `server/routes/ai_chat.py`
- Test: `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: Write the failing test** — append to `server/tests/test_routes_ai_chat.py` (uses the existing `setup` fixture; mirror neighbouring tests):

```python
def test_send_message_starts_persist_listener(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsmsg'; ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_42', 'active', str(ws))
    with patch('routes.ai_chat.ensure_listener') as ens, \
         patch('routes.ai_chat.OpenCodeClient'):
        resp = client.post('/ai/chat/sessions/sess_x/messages',
                           json={'content': 'hi'}, headers=dev_h)
    assert resp.status_code == 202
    ens.assert_called_once()
    assert ens.call_args[0][0] == 'sess_x'        # sid
    assert ens.call_args[0][1] == 'oc_42'         # opencode session id
    assert ens.call_args[0][2] == str(ws)         # workspace dir


def test_delete_session_stops_persist_listener(setup):
    client, cursor, oc, dev_h, _, ws_root = setup
    ws = ws_root / 'wsdel'; ws.mkdir(parents=True, exist_ok=True)
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_42', 'active', str(ws))
    with patch('routes.ai_chat.stop_listener') as stp, \
         patch('routes.ai_chat.OpenCodeClient'), \
         patch('routes.ai_chat.revoke_token'), \
         patch('routes.ai_chat.cleanup_session_workspace'):
        resp = client.delete('/ai/chat/sessions/sess_x', headers=dev_h)
    assert resp.status_code == 204
    stp.assert_called_once_with('sess_x')
```

> 注：若 `setup` 的解包元组形状与上面不同，按该文件既有测试（如 `test_run_script_requires_code`）实际的解包改写这两行，断言保持不变。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -k "persist_listener" -v`
Expected: FAIL (`ensure_listener`/`stop_listener` not importable in `routes.ai_chat`, or not called)

- [ ] **Step 3a: Import the helpers** — in `server/routes/ai_chat.py`, find the import:
```python
from utils.workspace_changes import git_changes, file_diff
```
and add immediately after it:
```python
from utils.chat_persist import (
    ensure_listener, stop_listener, new_state, apply_event, persist_turn, _event_session_id,
)
```

- [ ] **Step 3b: Start the listener when a turn begins (send_message)** — in `send_message`, find:
```python
    OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(
        sess[2], prompt.strip(), model=effective_model, directory=sess[4],
    )
    return jsonify({'messageId': msg_id, 'model': effective_model or None}), 202
```
and insert the `ensure_listener` call between them:
```python
    OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(
        sess[2], prompt.strip(), model=effective_model, directory=sess[4],
    )
    ensure_listener(sid, sess[2], sess[4])
    return jsonify({'messageId': msg_id, 'model': effective_model or None}), 202
```

- [ ] **Step 3c: Start the listener for slash commands (run_session_command)** — in `run_session_command`, find:
```python
    OpenCodeClient(OPENCODE_BASE_URL).run_command(
        sess[2], command, arguments, model=OPENCODE_MODEL, directory=sess[4],
    )
    return jsonify({'messageId': msg_id}), 202
```
and insert:
```python
    OpenCodeClient(OPENCODE_BASE_URL).run_command(
        sess[2], command, arguments, model=OPENCODE_MODEL, directory=sess[4],
    )
    ensure_listener(sid, sess[2], sess[4])
    return jsonify({'messageId': msg_id}), 202
```

- [ ] **Step 3d: Stop the listener on delete** — in `delete_session`, find:
```python
    opencode_session_id = sess[2]
    if opencode_session_id:
        try:
            OpenCodeClient(OPENCODE_BASE_URL).delete_session(opencode_session_id)
        except Exception:
            pass  # 404 from OpenCode = already gone (§7 #11)
```
and insert `stop_listener(sid)` right before the OpenCode delete:
```python
    opencode_session_id = sess[2]
    stop_listener(sid)
    if opencode_session_id:
        try:
            OpenCodeClient(OPENCODE_BASE_URL).delete_session(opencode_session_id)
        except Exception:
            pass  # 404 from OpenCode = already gone (§7 #11)
```

- [ ] **Step 3e: Refactor the SSE proxy to share helpers + idempotent persist** — in `sse_events`'s `generate()`, replace the whole accumulation/persist body. Find:
```python
        assistant_msg_ids = set()
        parts_by_id = {}
        part_order = []
        try:
            for evt in client.subscribe_events(directory=sess[4]):
                etype = evt.get('event', '')
                obj = evt.get('data') or {}
                props = obj.get('properties') or {}

                ev_sid = _event_session_id(props)
                if ev_sid and ev_sid != opencode_session_id:
                    continue

                if etype == 'message.updated':
                    info = props.get('info') or {}
                    if info.get('role') == 'assistant' and info.get('id'):
                        assistant_msg_ids.add(info['id'])
                elif etype == 'message.part.updated':
                    part = props.get('part') or {}
                    pid = part.get('id')
                    if pid and part.get('messageID') in assistant_msg_ids:
                        ptype = part.get('type')
                        if ptype == 'text':
                            if pid not in parts_by_id:
                                part_order.append(pid)
                            parts_by_id[pid] = {'type': 'text', 'text': part.get('text', '')}
                        elif ptype == 'tool':
                            if pid not in parts_by_id:
                                part_order.append(pid)
                            st = part.get('state') or {}
                            parts_by_id[pid] = {
                                'type': 'tool_use',
                                'name': part.get('tool') or 'tool',
                                'title': st.get('title'),
                                'status': st.get('status'),
                                'input': st.get('input'),
                                'result': st.get('output') if st.get('output') is not None else st.get('result'),
                            }
                elif etype == 'session.idle':
                    content = _build_assistant_content(parts_by_id, part_order)
                    if content:
                        try:
                            _persist_assistant_message(sid, content)
                        except Exception:
                            pass  # don't break the stream on DB hiccup (§7 #9)
                    assistant_msg_ids = set()
                    parts_by_id = {}
                    part_order = []

                yield _format_sse(etype, props)
        except GeneratorExit:
            return
```
and replace it with:
```python
        state = new_state()
        try:
            for evt in client.subscribe_events(directory=sess[4]):
                etype = evt.get('event', '')
                props = (evt.get('data') or {}).get('properties') or {}

                ev_sid = _event_session_id(props)
                if ev_sid and ev_sid != opencode_session_id:
                    continue

                if apply_event(state, evt, opencode_session_id) == 'idle':
                    persist_turn(sid, state)   # idempotent: dedups with the listener
                    state = new_state()

                yield _format_sse(etype, props)
        except GeneratorExit:
            return
```

- [ ] **Step 3f: Remove the now-unused proxy helpers** — delete the `_build_assistant_content`, `_persist_assistant_message`, and `_event_session_id` function definitions from `server/routes/ai_chat.py` (lines around `:388-427`). `_event_session_id` is now imported from `chat_persist`; the other two are replaced by `build_content`/`persist_turn` inside `chat_persist`. Keep the `import secrets`/`json` usages elsewhere intact.

> If any other code in `ai_chat.py` still references `_build_assistant_content` or `_persist_assistant_message`, grep first (`grep -n "_build_assistant_content\|_persist_assistant_message" server/routes/ai_chat.py`); there should be no remaining references after Step 3e.

- [ ] **Step 4: Run the new tests + the full ai_chat route suite + chat_persist suite**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py tests/test_chat_persist.py -v`
Expected: PASS (all, including the 2 new wiring tests; no regressions)

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): wire server-side persistence listener into chat routes"
```

---

## Self-Review 结果

- **Spec 覆盖：** A 新单元 chat_persist（Task 1/2/3）；B 共享累积 helper + 代理改用（Task 1, Task 4 Step 3e）；C 幂等 upsert 安全网（Task 2, Task 4 Step 3e）；D 接线 send_message/run_session_command/delete_session（Task 4 Step 3b-3d）；E 生命周期：不活跃超时（Task 3 `INACTIVITY_TIMEOUT` + `read_timeout`）、删除停止（Task 4 3d）、错误退出（`_listener_thread` try/except）；F 测试（Task 1/2/3/4）。Flask 重启限制已在 spec 记录，无需代码。
- **Placeholder 扫描：** 无 TBD/TODO；每个改动步骤含完整代码。Task 4 的 fixture 注解为说明性，断言明确。
- **类型/命名一致：** `new_state`/`apply_event`/`build_content`/`persist_turn`/`_run_listener`/`_listener_thread`/`ensure_listener`/`stop_listener`/`_event_session_id`/`INACTIVITY_TIMEOUT`/`read_timeout` 在定义与引用处一致；state 字典键（`assistant_msg_ids`/`parts_by_id`/`part_order`/`turn_msg_id`）跨函数一致；`persist_turn(sid, state)` 与监听/代理调用签名一致。
