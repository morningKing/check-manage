# 人工接管 子项目 C2：管理端控制台实时（SSE）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 B2 管理端接管控制台订阅 C1 事件总线的实例频道（`inst:<iid>`），实时刷新队列与当前对话，取代 5s 轮询；管理端 SSE 用短时一次性 ticket 鉴权。

**Architecture:** 复用 `kefu_event_bus`（key `inst:<iid>`）；访客/管理端 5 处新增实例事件 `{sid,type}`；新 `kefu_sse_ticket` 模块 + `POST /admin/kefu/events/ticket` + `GET /admin/kefu/events` SSE；`KefuTakeoverPanel` 轮询→EventSource（手动重连 + 取 ticket）。

**Tech Stack:** Python Flask + `queue`/`threading` + `secrets`；Vue 3 + EventSource；Vitest / Pytest。

## Global Constraints

- **单进程**，复用 C1 `kefu_event_bus`（`publish(key,event)` key 任意字符串）——**实例频道 key = `f"inst:{iid}"`**，事件 `{'sid': <sid>, 'type': <type>}`。
- 会话频道（C1，key=sid，事件 `{'type':...}`）保持不变；实例事件是**新增**、与会话事件并发。
- 管理端 SSE 用**短时一次性 ticket**（TTL 60s）：`POST .../events/ticket`（需 `admin.kefu`）签发；`GET .../events?instance=&ticket=` 消费（过期/已用/未知→401）。
- 管理端 SSE 独立并发上限 `MAX_ADMIN_SSE_PER_USER = 3`；心跳 `ADMIN_SSE_HEARTBEAT = 20`；SSE 生成器 `finally` 必 `unsubscribe` + 释放并发位。
- 前端**彻底删除 5s 轮询**；EventSource 手动重连（ticket 一次性）：onerror→close→退避→重取 ticket 重开。
- 后端测试：`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest <file> -v`（Bash 前缀 env，非 cmd `set`）。前端：repo 根 `npx vitest run <file>`。构建 `npm run build`。**后端改动需重启 `python app.py`**（Playwright 前）。提交只 `git add` 该任务确切文件。

---

### Task 1: 实例事件发布点（访客 2 处 + 管理端 3 处）

**Files:**
- Modify: `server/routes/kefu_admin.py`（takeover/release/human_reply 加实例发布；takeover/release 需保留 sess 取 iid）
- Modify: `server/routes/kefu_public.py`（send_message/request_human 加实例发布）
- Modify: `server/tests/test_kefu_takeover_admin.py`（更新 3 个已有 publish 断言：现在发 2 次）
- Modify: `server/tests/test_kefu_public_chat.py`（+2 实例发布断言）

**Interfaces:**
- Produces: 实例频道事件 `publish(f"inst:{iid}", {'sid': sid, 'type': <type>})`，type ∈ `visitor_message|needs_human|takeover|release|human_message`。

- [ ] **Step 1: 更新/新增失败测试**

(a) `server/tests/test_kefu_takeover_admin.py`——把已有的 3 个 publish 用例整体替换为（现在每路由 publish 2 次：会话 + 实例）：

```python
def test_takeover_publishes_events(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.takeover_session', return_value=True), \
         patch('routes.kefu_admin.log_operation'), \
         patch('routes.kefu_admin.kefu_event_bus.publish') as P:
        client.post('/admin/kefu/sessions/sess_1/takeover', headers=admin_headers)
    P.assert_any_call('sess_1', {'type': 'takeover'})
    P.assert_any_call('inst:kf_1', {'sid': 'sess_1', 'type': 'takeover'})
    assert P.call_count == 2


def test_release_publishes_events(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.release_session', return_value=True), \
         patch('routes.kefu_admin.log_operation'), \
         patch('routes.kefu_admin.kefu_event_bus.publish') as P:
        client.post('/admin/kefu/sessions/sess_1/release', headers=admin_headers)
    P.assert_any_call('sess_1', {'type': 'release'})
    P.assert_any_call('inst:kf_1', {'sid': 'sess_1', 'type': 'release'})
    assert P.call_count == 2


def test_human_reply_publishes_events(client, admin_headers):
    s = dict(_SESS, human_takeover=True)
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=s), \
         patch('routes.kefu_admin.kefu_repo.insert_human_message', return_value='msg_1'), \
         patch('routes.kefu_admin.kefu_event_bus.publish') as P:
        client.post('/admin/kefu/sessions/sess_1/messages', json={'content': '您好'}, headers=admin_headers)
    P.assert_any_call('sess_1', {'type': 'human_message'})
    P.assert_any_call('inst:kf_1', {'sid': 'sess_1', 'type': 'human_message'})
    assert P.call_count == 2
```

（`_SESS` 已含 `'kefu_instance_id': 'kf_1'`。）

(b) `server/tests/test_kefu_public_chat.py`——追加（`SESS = ('sess_1','kefu-bot','oc_1','active','/ws/kf','kf_1', False)`，`sess[5]='kf_1'`）：

```python
def test_send_message_publishes_instance_event(client, mock_cursor):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS), \
         patch('routes.kefu_public.kefu_repo.get_instance', return_value=INST), \
         patch('routes.kefu_public.OpenCodeClient'), \
         patch('routes.kefu_public.ensure_listener'), \
         patch('routes.kefu_public.kefu_event_bus.publish') as P:
        client.post('/kefu/sessions/sess_1/messages', json={'content': '价格'},
                    headers={'X-Visitor-Id': 'v1'})
    P.assert_any_call('inst:kf_1', {'sid': 'sess_1', 'type': 'visitor_message'})


def test_request_human_publishes_instance_event(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=SESS), \
         patch('routes.kefu_public.kefu_repo.set_needs_human'), \
         patch('routes.kefu_public.kefu_event_bus.publish') as P:
        resp = client.post('/kefu/sessions/sess_1/request-human', headers={'X-Visitor-Id': 'v1'})
    assert resp.status_code == 200
    P.assert_any_call('inst:kf_1', {'sid': 'sess_1', 'type': 'needs_human'})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_takeover_admin.py tests/test_kefu_public_chat.py -v -k "publishes"`
Expected: 新/改的 5 个用例 FAIL（实例发布未实现；旧 3 个断言已被替换）。

- [ ] **Step 3: 管理端实例发布**

`server/routes/kefu_admin.py`：

`takeover_session_route`——把
```python
def takeover_session_route(sid):
    if not kefu_repo.get_kefu_session_admin(sid):
        return jsonify({'error': 'not found'}), 404
    kefu_repo.takeover_session(sid, g.current_user['userId'])
    log_operation('takeover', 'kefu_session', sid, None, '接管客服会话')
    kefu_event_bus.publish(sid, {'type': 'takeover'})
    return jsonify({'humanTakeover': True})
```
改为
```python
def takeover_session_route(sid):
    sess = kefu_repo.get_kefu_session_admin(sid)
    if not sess:
        return jsonify({'error': 'not found'}), 404
    kefu_repo.takeover_session(sid, g.current_user['userId'])
    log_operation('takeover', 'kefu_session', sid, None, '接管客服会话')
    kefu_event_bus.publish(sid, {'type': 'takeover'})
    kefu_event_bus.publish(f"inst:{sess['kefu_instance_id']}", {'sid': sid, 'type': 'takeover'})
    return jsonify({'humanTakeover': True})
```

`release_session_route`——同样把 `if not kefu_repo.get_kefu_session_admin(sid):` 改为 `sess = kefu_repo.get_kefu_session_admin(sid)` + `if not sess:`，并在 `kefu_event_bus.publish(sid, {'type': 'release'})` 后加：
```python
    kefu_event_bus.publish(f"inst:{sess['kefu_instance_id']}", {'sid': sid, 'type': 'release'})
```

`human_reply`（已有 `sess`）——在 `kefu_event_bus.publish(sid, {'type': 'human_message'})` 后加：
```python
    kefu_event_bus.publish(f"inst:{sess['kefu_instance_id']}", {'sid': sid, 'type': 'human_message'})
```

- [ ] **Step 4: 访客实例发布**

`server/routes/kefu_public.py`：

`send_message`——在用户消息 `INSERT` 的 `with get_db() as conn: ...` 块**之后**、`if sess[6]:` 之前加：
```python
    kefu_event_bus.publish(f"inst:{sess[5]}", {'sid': sid, 'type': 'visitor_message'})
```

`request_human`——把
```python
    kefu_repo.set_needs_human(sid, True)
    return jsonify({'needsHuman': True}), 200
```
改为
```python
    kefu_repo.set_needs_human(sid, True)
    kefu_event_bus.publish(f"inst:{sess[5]}", {'sid': sid, 'type': 'needs_human'})
    return jsonify({'needsHuman': True}), 200
```

- [ ] **Step 5: 运行测试通过 + 回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_takeover_admin.py tests/test_kefu_public_chat.py -v`
Expected: 全 PASS（含更新后的 publish 用例）。

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q -k kefu`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add server/routes/kefu_admin.py server/routes/kefu_public.py server/tests/test_kefu_takeover_admin.py server/tests/test_kefu_public_chat.py
git commit -m "feat(kefu): 实例频道事件（访客发消息/转人工 + 管理端接管/回复/释放）"
```

---

### Task 2: 短时 ticket 模块 + 管理端 SSE 端点

**Files:**
- Create: `server/utils/kefu_sse_ticket.py`
- Create: `server/tests/test_kefu_sse_ticket.py`
- Modify: `server/routes/kefu_admin.py`（imports + `_format_sse` + 并发计数 + ticket 端点 + `/events` SSE）
- Modify: `server/tests/test_kefu_takeover_admin.py`（+ticket 端点 + /events 401/400 用例）

**Interfaces:**
- Consumes: `kefu_event_bus.subscribe/unsubscribe`（C1）。
- Produces: `kefu_sse_ticket.issue(user_id)->str` / `consume(ticket)->str|None`；`POST /admin/kefu/events/ticket`→`{ticket}`；`GET /admin/kefu/events?instance=&ticket=`（SSE）。

- [ ] **Step 1: 写 ticket 模块失败测试**

`server/tests/test_kefu_sse_ticket.py`:

```python
import time
from utils import kefu_sse_ticket as tk


def test_issue_then_consume_returns_user():
    t = tk.issue('user-admin')
    assert isinstance(t, str) and t
    assert tk.consume(t) == 'user-admin'


def test_consume_is_one_time():
    t = tk.issue('u1')
    assert tk.consume(t) == 'u1'
    assert tk.consume(t) is None


def test_unknown_ticket_returns_none():
    assert tk.consume('nope') is None


def test_expired_ticket_returns_none(monkeypatch):
    t = tk.issue('u2')
    monkeypatch.setattr(tk.time, 'time', lambda: time.time() + tk._TTL + 1)
    assert tk.consume(t) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_sse_ticket.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 ticket 模块**

`server/utils/kefu_sse_ticket.py`:

```python
"""管理端 SSE 短时一次性 ticket（进程内）。

EventSource 无法带 Authorization 头，故先经已鉴权接口换 ticket，
再用 ?ticket= 开 SSE。ticket 一次性、TTL 60s，绑定 user_id。
"""
import secrets
import threading
import time

_TTL = 60
_tickets: dict = {}   # ticket -> (user_id, expiry_ts)
_lock = threading.Lock()


def issue(user_id: str) -> str:
    t = secrets.token_urlsafe(24)
    with _lock:
        _tickets[t] = (user_id, time.time() + _TTL)
    return t


def consume(ticket: str):
    """一次性消费：命中且未过期→返回 user_id 并删除；否则 None。顺带清过期项。"""
    now = time.time()
    with _lock:
        for k in [k for k, (_, exp) in _tickets.items() if exp < now]:
            _tickets.pop(k, None)
        item = _tickets.pop(ticket, None)
    if not item:
        return None
    user_id, exp = item
    return user_id if exp >= now else None
```

- [ ] **Step 4: 运行 ticket 测试通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_sse_ticket.py -v`
Expected: PASS（4 用例）。

- [ ] **Step 5: 写端点失败测试**

在 `server/tests/test_kefu_takeover_admin.py` 追加：

```python
def test_events_ticket_requires_admin(client, dev_headers):
    assert client.post('/admin/kefu/events/ticket', headers=dev_headers).status_code == 403


def test_events_ticket_returns_token(client, admin_headers):
    resp = client.post('/admin/kefu/events/ticket', headers=admin_headers)
    assert resp.status_code == 200
    assert resp.get_json()['ticket']


def test_events_bad_ticket_401(client):
    with patch('routes.kefu_admin.kefu_sse_ticket.consume', return_value=None):
        resp = client.get('/admin/kefu/events?instance=kf_1&ticket=bad')
    assert resp.status_code == 401


def test_events_missing_instance_400(client):
    with patch('routes.kefu_admin.kefu_sse_ticket.consume', return_value='user-admin'):
        resp = client.get('/admin/kefu/events?ticket=good')
    assert resp.status_code == 400


def test_admin_sse_cap_allows_three_then_blocks():
    from routes import kefu_admin as ka
    ka._admin_sse_active.clear()
    assert ka._admin_sse_acquire('u') is True
    assert ka._admin_sse_acquire('u') is True
    assert ka._admin_sse_acquire('u') is True
    assert ka._admin_sse_acquire('u') is False   # cap = 3
    ka._admin_sse_release('u')
    assert ka._admin_sse_acquire('u') is True
    ka._admin_sse_active.clear()
```

- [ ] **Step 6: 运行确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_takeover_admin.py -v -k "events or cap"`
Expected: FAIL（端点/计数未实现）。

- [ ] **Step 7: 实现端点 + 计数**

`server/routes/kefu_admin.py`：

(a) 顶部 import 段改/加：
```python
import re
import json
import threading
import queue as _queue
from flask import Blueprint, request, jsonify, g, Response, stream_with_context
from auth import require_permission
from utils import kefu_repo
from utils import kefu_event_bus
from utils import kefu_sse_ticket
from utils.operation_log import log_operation
```

(b) 在 `_BLOCK_TYPES = {...}` 之后加常量 + 计数助手 + `_format_sse`：
```python
MAX_ADMIN_SSE_PER_USER = 3
ADMIN_SSE_HEARTBEAT = 20
_admin_sse_active: dict = {}
_admin_sse_lock = threading.Lock()


def _format_sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _admin_sse_acquire(uid: str) -> bool:
    with _admin_sse_lock:
        n = _admin_sse_active.get(uid, 0)
        if n >= MAX_ADMIN_SSE_PER_USER:
            return False
        _admin_sse_active[uid] = n + 1
        return True


def _admin_sse_release(uid: str) -> None:
    with _admin_sse_lock:
        n = _admin_sse_active.get(uid, 0)
        if n <= 1:
            _admin_sse_active.pop(uid, None)
        else:
            _admin_sse_active[uid] = n - 1
```

(c) 在文件末尾加两个端点：
```python
@kefu_admin_bp.route('/events/ticket', methods=['POST'])
@require_permission('admin.kefu')
def events_ticket():
    return jsonify({'ticket': kefu_sse_ticket.issue(g.current_user['userId'])})


@kefu_admin_bp.route('/events', methods=['GET'])
def admin_events():
    uid = kefu_sse_ticket.consume(request.args.get('ticket') or '')
    if not uid:
        return jsonify({'error': 'invalid ticket'}), 401
    iid = (request.args.get('instance') or '').strip()
    if not iid:
        return jsonify({'error': 'instance required'}), 400
    if not _admin_sse_acquire(uid):
        return jsonify({'error': '并发连接过多，请稍后再试'}), 429

    q = kefu_event_bus.subscribe(f"inst:{iid}")

    def generate():
        try:
            yield _format_sse('ready', {})
            while True:
                try:
                    evt = q.get(timeout=ADMIN_SSE_HEARTBEAT)
                    yield _format_sse(evt.get('type', 'message'), evt)
                except _queue.Empty:
                    yield ': ping\n\n'
        except GeneratorExit:
            return
        except Exception:
            return
        finally:
            kefu_event_bus.unsubscribe(f"inst:{iid}", q)
            _admin_sse_release(uid)

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
```

- [ ] **Step 8: 运行测试通过 + 回归**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_kefu_takeover_admin.py tests/test_kefu_sse_ticket.py -v`
Expected: 全 PASS。

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q -k kefu`
Expected: 全 PASS。

- [ ] **Step 9: Commit**

```bash
git add server/utils/kefu_sse_ticket.py server/tests/test_kefu_sse_ticket.py server/routes/kefu_admin.py server/tests/test_kefu_takeover_admin.py
git commit -m "feat(kefu): 管理端 SSE 端点 /admin/kefu/events + 短时 ticket 鉴权"
```

---

### Task 3: 前端 — 控制台轮询改 SSE

**Files:**
- Modify: `src/api/kefu.ts`（`requestSseTicket` + `createKefuAdminEventStream`）
- Modify: `src/components/kefu/KefuTakeoverPanel.vue`（删轮询、开 SSE、instance 切换重开、卸载关流）
- Modify: `src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`（mock + 用例改造）

**Interfaces:**
- Consumes: 后端 `/admin/kefu/events`（Task 2）。
- Produces: `requestSseTicket(): Promise<{ticket:string}>`；`createKefuAdminEventStream(instanceId, { onReady?, onEvent? }): () => void`（onEvent 收 `{sid?,type?}`）。

- [ ] **Step 1: 改测试（先失败）**

在 `src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`：

(a) `vi.mock('@/api/kefu', () => ({ ... }))` 里追加两行：
```typescript
  requestSseTicket: (...a: any[]) => mockTicket(...a),
  createKefuAdminEventStream: (...a: any[]) => mockStream(...a),
```
并在文件顶部的 mock 声明区（`const mockList = ...` 那行附近）加：
```typescript
const mockTicket = vi.fn(), mockStream = vi.fn()
```
在 `beforeEach` 里补默认返回：
```typescript
    mockTicket.mockResolvedValue({ ticket: 't' })
    mockStream.mockReturnValue(() => {})
```

(b) 删除已有的 `it('polls queue every 5s and stops on unmount', ...)` 整段。

(c) 追加：
```typescript
  it('opens admin event stream on mount and closes on unmount', async () => {
    const close = vi.fn(); mockStream.mockReturnValue(close)
    const w = mountP('kf_1'); await flushPromises()
    expect(mockStream).toHaveBeenCalled()
    expect(mockStream.mock.calls[0][0]).toBe('kf_1')
    w.unmount()
    expect(close).toHaveBeenCalled()
  })

  it('onReady reloads queue', async () => {
    const w = mountP(); await flushPromises()
    const h = mockStream.mock.calls[0][1]
    mockList.mockClear()
    h.onReady(); await flushPromises()
    expect(mockList).toHaveBeenCalled()
  })

  it('onEvent for non-selected session reloads queue only', async () => {
    const w = mountP(); await flushPromises()
    const h = mockStream.mock.calls[0][1]
    mockList.mockClear(); mockMsgs.mockClear()
    h.onEvent({ sid: 'other', type: 'visitor_message' }); await flushPromises()
    expect(mockList).toHaveBeenCalled()
    expect(mockMsgs).not.toHaveBeenCalled()
  })

  it('onEvent for the selected session reloads queue + conversation', async () => {
    const w = mountP(); await flushPromises()
    await (w.vm as any).selectSession('sess_1')
    const h = mockStream.mock.calls[0][1]
    mockList.mockClear(); mockMsgs.mockClear()
    h.onEvent({ sid: 'sess_1', type: 'human_message' }); await flushPromises()
    expect(mockList).toHaveBeenCalled()
    expect(mockMsgs).toHaveBeenCalled()
  })
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`
Expected: FAIL（`createKefuAdminEventStream` 未在组件调用 / 轮询用例已删但流未接）。

- [ ] **Step 3: `api/kefu.ts` 加流 API**

在 `src/api/kefu.ts` 末尾追加：

```typescript
export function requestSseTicket() { return post<{ ticket: string }>('/admin/kefu/events/ticket', {}) }

const ADMIN_SSE_BACKOFF = [1000, 2000, 5000, 10000]

export function createKefuAdminEventStream(
  instanceId: string,
  handlers: { onReady?: () => void; onEvent?: (e: { sid?: string; type?: string }) => void }
): () => void {
  let es: EventSource | null = null, closed = false, attempt = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  const EVENTS = ['visitor_message', 'needs_human', 'takeover', 'release', 'human_message']
  const retry = () => {
    if (closed) return
    timer = setTimeout(open, ADMIN_SSE_BACKOFF[Math.min(attempt, ADMIN_SSE_BACKOFF.length - 1)]); attempt += 1
  }
  const open = async () => {
    if (closed) return
    let ticket = ''
    try { ticket = (await requestSseTicket()).ticket } catch { retry(); return }
    if (closed) return
    es = new EventSource(`/api/admin/kefu/events?instance=${encodeURIComponent(instanceId)}&ticket=${encodeURIComponent(ticket)}`)
    es.addEventListener('ready', () => handlers.onReady?.())
    for (const name of EVENTS) {
      es.addEventListener(name, (e: MessageEvent) => {
        let data: any = {}
        try { data = JSON.parse(e.data) } catch { /* ignore */ }
        handlers.onEvent?.(data)
      })
    }
    es.onopen = () => { attempt = 0 }
    es.onerror = () => { es?.close(); if (closed) return; retry() }
  }
  open()
  return () => { closed = true; if (timer) clearTimeout(timer); es?.close() }
}
```

- [ ] **Step 4: `KefuTakeoverPanel.vue` 轮询改 SSE**

(a) 删除两个计时器声明——删 `let queueTimer: ReturnType<typeof setInterval> | null = null` 与 `let convTimer: ReturnType<typeof setInterval> | null = null`（第 71–72 行）；在其位置加：
```typescript
let closeStream: (() => void) | null = null
```

(b) 加 `openStream`（放在 `loadConversation` 之后、`selectSession` 之前或就近）：
```typescript
function openStream() {
  closeStream?.()
  closeStream = api.createKefuAdminEventStream(props.instanceId, {
    onReady: () => { loadQueue().catch(() => {}); if (selectedSid.value) loadConversation().catch(() => {}) },
    onEvent: (e) => { loadQueue().catch(() => {}); if (e.sid && e.sid === selectedSid.value) loadConversation().catch(() => {}) },
  })
}
```

(c) `watch(instanceId)`——把
```typescript
watch(() => props.instanceId, () => { selectedSid.value = ''; messages.value = []; loadQueue() })
```
改为
```typescript
watch(() => props.instanceId, () => { selectedSid.value = ''; messages.value = []; loadQueue().catch(() => {}); openStream() })
```

(d) `onMounted`——把
```typescript
onMounted(() => {
  loadQueue().catch(() => {})
  queueTimer = setInterval(() => { loadQueue().catch(() => {}) }, 5000)
  convTimer = setInterval(() => { if (selectedSid.value) loadConversation().catch(() => {}) }, 5000)
})
```
改为
```typescript
onMounted(() => {
  loadQueue().catch(() => {})
  openStream()
})
```

(e) `onBeforeUnmount`——把
```typescript
onBeforeUnmount(() => {
  if (queueTimer) clearInterval(queueTimer)
  if (convTimer) clearInterval(convTimer)
})
```
改为
```typescript
onBeforeUnmount(() => { closeStream?.() })
```

- [ ] **Step 5: 运行测试通过 + 构建**

Run: `npx vitest run src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`
Expected: PASS（现有保留用例 + 4 新用例；无 `queueTimer/convTimer` 残留报错）。

Run: `npm run build`
Expected: vue-tsc 无错误。

- [ ] **Step 6: Commit**

```bash
git add src/api/kefu.ts src/components/kefu/KefuTakeoverPanel.vue src/components/kefu/__tests__/KefuTakeoverPanel.test.ts
git commit -m "feat(kefu): 管理端控制台改用 SSE 实时（去 5s 轮询）"
```

---

### Task 4: 文档 + Playwright 实机验证

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`（第 10 节控制台改「SSE 实时」）
- Evidence: `.playwright-mcp/` 截图

- [ ] **Step 1: 重启后端并确认服务**

后端改了代码——**重启** `python app.py`（:3002）使 `/admin/kefu/events` 生效；确认前端 `:5173`、OpenCode `:4096`。

- [ ] **Step 2: Playwright 走控制台实时闭环**

1. 管理端登录（admin/admin123）→ 设置中心 → 集成对接 → 智能客服 → 选「演示客服」→「人工接管」tab（开启 admin SSE）。
2. 用第二个浏览器标签打开访客 `/kefu/demo`（新访客 id）→ 发一条消息 → 点「🙋 转人工」。
3. 回到管理端 tab：断言队列**实时**（≪5s、未手动刷新）冒出该待人工会话（徽标「待人工」）。
4. 控制台点该会话接管；切访客标签发一条消息；回管理端断言对话区**实时**出现该访客消息。
5. 网络面板断言存在 `admin/kefu/events` SSE，且**无** 5s 周期的 `GET /admin/kefu/sessions` 轮询。
6. 截图存 `.playwright-mcp/kefu-console-realtime.png`；事后清理测试会话。

- [ ] **Step 3: 更新用户指南**

在 `docs/user-guide/ai/smart-customer-service.md` 第 10 节：把管理端控制台「约 5s 自动刷新」改为「**SSE 实时**（订阅事件总线的实例频道；短时 ticket 鉴权）」；标注人工接管 Phase 2 前端全链路（访客侧 + 控制台）已全实时。

- [ ] **Step 4: Commit（文档）**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 管理端控制台改为 SSE 实时"
```

---

## Self-Review

**Spec coverage:**
- 实例频道复用（key `inst:<iid>`）+ 5 处发布 → Task 1 ✓
- ticket 模块（issue/consume/一次性/TTL）→ Task 2 Step 3 ✓
- ticket 端点 + `/events` SSE + 并发上限 + finally 清理 → Task 2 Step 7 ✓
- `requestSseTicket` + `createKefuAdminEventStream`（手动重连 + 取 ticket）→ Task 3 Step 3 ✓
- Panel 轮询→SSE + onEvent(命中会话 reload 对话) + instance 切换重开 + 卸载关流 → Task 3 Step 4 ✓
- 文档 + Playwright（含无轮询断言）→ Task 4 ✓

**Placeholder scan:** 无 TBD/TODO；每步完整代码；测试含具体断言。✓

**Type consistency:**
- `issue(user_id)->str` / `consume(ticket)->str|None`（Task 2）与端点用法一致 ✓
- 实例事件 `{'sid':sid,'type':t}` 发布（Task 1）与前端 `onEvent({sid,type})`（Task 3）、后端 `_format_sse(evt['type'], evt)`（Task 2）一致 ✓
- 事件名 `visitor_message/needs_human/takeover/release/human_message`（Task 1 发布）与前端 `EVENTS` 数组（Task 3）一致 ✓
- `createKefuAdminEventStream(instanceId, {onReady,onEvent})` / `requestSseTicket`（Task 3）与 Panel 用法、测试 mock 一致 ✓
- `_admin_sse_acquire/_release`/`_admin_sse_active`/`MAX_ADMIN_SSE_PER_USER`（Task 2）命名一致 ✓
- `closeStream`/`openStream` 命名在 Panel 内一致，`queueTimer/convTimer` 已全删 ✓
