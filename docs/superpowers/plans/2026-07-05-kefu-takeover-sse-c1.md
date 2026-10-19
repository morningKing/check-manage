# 人工接管 子项目 C1：事件总线 + 访客实时（SSE）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建进程内事件总线，管理端接管/回复/释放发布事件，访客经独立 SSE 通道**实时**收到（取代 3s 轮询），并让访客页在释放后复原。

**Architecture:** `kefu_event_bus`（dict[sid]→set[Queue] + lock）；`kefu_admin` 三处路由 publish；`kefu_public` 新增 `/human-events` SSE（独立并发上限）消费总线；访客 `KefuChatPage` 从轮询改 EventSource。

**Tech Stack:** Python Flask + `queue`/`threading`（进程内 pub/sub）；Vue 3 + EventSource；Vitest / Pytest。

## Global Constraints

- **单进程**（waitress/Werkzeug）→ 进程内 pub/sub，无 Redis。
- 事件**只做信号**：`{'type': 'human_message'|'takeover'|'release'}`；客户端收到后 `reload()` 拉历史（不携带消息体）。
- **不改** OpenCode 代理 SSE（`events()`）、后端状态机/repo 语义、B2 控制台。
- 访客人工事件流独立并发上限 `MAX_HUMAN_SSE_PER_VISITOR = 2`，**不占** OpenCode 流的 `MAX_SSE_PER_VISITOR=3` 配额。
- 有界订阅队列（`maxsize=100`）`put_nowait`，满则丢（信号型无害）。SSE 生成器 `finally` 必 `unsubscribe` + 释放配额。
- 后端测试：`cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest <file> -v`。前端：repo 根 `npx vitest run <file>`。构建 `npm run build`。提交只 `git add` 该任务确切文件。**后端改动需重启 `python app.py` 才生效**（Playwright 前）。

---

### Task 1: 事件总线 `kefu_event_bus` + 管理端发布点

**Files:**
- Create: `server/utils/kefu_event_bus.py`
- Modify: `server/routes/kefu_admin.py`（import + 3 处 publish）
- Create: `server/tests/test_kefu_event_bus.py`
- Modify: `server/tests/test_kefu_takeover_admin.py`（+3 publish 断言用例）

**Interfaces:**
- Produces: `kefu_event_bus.subscribe(sid) -> queue.Queue`、`unsubscribe(sid, q) -> None`、`publish(sid, event: dict) -> None`。

- [ ] **Step 1: 写总线失败测试**

`server/tests/test_kefu_event_bus.py`:

```python
import queue
from utils import kefu_event_bus as bus


def test_publish_delivers_to_subscribers_of_that_sid():
    q1 = bus.subscribe('s1')
    q2 = bus.subscribe('s1')
    qx = bus.subscribe('other')
    bus.publish('s1', {'type': 'takeover'})
    assert q1.get_nowait() == {'type': 'takeover'}
    assert q2.get_nowait() == {'type': 'takeover'}
    assert qx.empty()
    bus.unsubscribe('s1', q1); bus.unsubscribe('s1', q2); bus.unsubscribe('other', qx)


def test_publish_no_subscribers_is_noop():
    bus.publish('nobody', {'type': 'release'})   # must not raise


def test_full_queue_drops_without_raising():
    q = bus.subscribe('sfull')
    for _ in range(bus._MAX_Q + 5):
        bus.publish('sfull', {'type': 'human_message'})   # must not raise
    assert q.qsize() == bus._MAX_Q
    bus.unsubscribe('sfull', q)


def test_unsubscribe_removes_and_cleans_empty_sid():
    q = bus.subscribe('sclean')
    assert 'sclean' in bus._subscribers
    bus.unsubscribe('sclean', q)
    assert 'sclean' not in bus._subscribers
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_event_bus.py -v`
Expected: FAIL（`ModuleNotFoundError: utils.kefu_event_bus`）。

- [ ] **Step 3: 实现总线**

`server/utils/kefu_event_bus.py`:

```python
"""进程内、按客服 session 分发的极简 pub/sub 事件总线。

单进程部署（waitress/Werkzeug）下，用于把管理端的人工回复/接管/释放事件
实时投递给访客 SSE 连接。事件为“信号”，客户端收到后自行 reload 历史。
"""
import queue
import threading

_subscribers: dict[str, set] = {}   # sid -> set[queue.Queue]
_lock = threading.Lock()
_MAX_Q = 100                        # 每订阅者有界队列，防内存膨胀


def subscribe(sid: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=_MAX_Q)
    with _lock:
        _subscribers.setdefault(sid, set()).add(q)
    return q


def unsubscribe(sid: str, q: queue.Queue) -> None:
    with _lock:
        subs = _subscribers.get(sid)
        if subs:
            subs.discard(q)
            if not subs:
                _subscribers.pop(sid, None)


def publish(sid: str, event: dict) -> None:
    with _lock:
        subs = list(_subscribers.get(sid, ()))   # 快照，锁外投递
    for q in subs:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass   # 信号型事件，丢弃无害（下条/重连 reload 兜底）
```

- [ ] **Step 4: 运行总线测试通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_event_bus.py -v`
Expected: PASS（4 用例）。

- [ ] **Step 5: 写发布点断言测试**

在 `server/tests/test_kefu_takeover_admin.py` 末尾追加（沿用文件顶部的 `_SESS`、`patch`）：

```python
def test_takeover_publishes_event(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.takeover_session', return_value=True), \
         patch('routes.kefu_admin.log_operation'), \
         patch('routes.kefu_admin.kefu_event_bus.publish') as P:
        client.post('/admin/kefu/sessions/sess_1/takeover', headers=admin_headers)
    P.assert_called_once_with('sess_1', {'type': 'takeover'})


def test_release_publishes_event(client, admin_headers):
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=dict(_SESS)), \
         patch('routes.kefu_admin.kefu_repo.release_session', return_value=True), \
         patch('routes.kefu_admin.log_operation'), \
         patch('routes.kefu_admin.kefu_event_bus.publish') as P:
        client.post('/admin/kefu/sessions/sess_1/release', headers=admin_headers)
    P.assert_called_once_with('sess_1', {'type': 'release'})


def test_human_reply_publishes_event(client, admin_headers):
    s = dict(_SESS, human_takeover=True)
    with patch('routes.kefu_admin.kefu_repo.get_kefu_session_admin', return_value=s), \
         patch('routes.kefu_admin.kefu_repo.insert_human_message', return_value='msg_1'), \
         patch('routes.kefu_admin.kefu_event_bus.publish') as P:
        client.post('/admin/kefu/sessions/sess_1/messages', json={'content': '您好'}, headers=admin_headers)
    P.assert_called_once_with('sess_1', {'type': 'human_message'})
```

- [ ] **Step 6: 运行确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_takeover_admin.py -v`
Expected: 3 新用例 FAIL（`AttributeError: ... has no attribute 'kefu_event_bus'`）。

- [ ] **Step 7: 接入发布点**

在 `server/routes/kefu_admin.py`：顶部 import 段加（与 `kefu_repo` 同处）：

```python
from utils import kefu_event_bus
```

`takeover_session_route`：把
```python
    log_operation('takeover', 'kefu_session', sid, None, '接管客服会话')
    return jsonify({'humanTakeover': True})
```
改为
```python
    log_operation('takeover', 'kefu_session', sid, None, '接管客服会话')
    kefu_event_bus.publish(sid, {'type': 'takeover'})
    return jsonify({'humanTakeover': True})
```

`release_session_route`：把
```python
    log_operation('release', 'kefu_session', sid, None, '释放客服会话')
    return jsonify({'humanTakeover': False})
```
改为
```python
    log_operation('release', 'kefu_session', sid, None, '释放客服会话')
    kefu_event_bus.publish(sid, {'type': 'release'})
    return jsonify({'humanTakeover': False})
```

`human_reply`：把
```python
    mid = kefu_repo.insert_human_message(sid, content, g.current_user['userId'])
    return jsonify({'messageId': mid}), 201
```
改为
```python
    mid = kefu_repo.insert_human_message(sid, content, g.current_user['userId'])
    kefu_event_bus.publish(sid, {'type': 'human_message'})
    return jsonify({'messageId': mid}), 201
```

- [ ] **Step 8: 运行发布点测试通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_takeover_admin.py -v`
Expected: PASS（原 7 + 新 3 = 10 用例）。

- [ ] **Step 9: Commit**

```bash
git add server/utils/kefu_event_bus.py server/routes/kefu_admin.py server/tests/test_kefu_event_bus.py server/tests/test_kefu_takeover_admin.py
git commit -m "feat(kefu): 进程内事件总线 + 管理端接管/回复/释放发布事件"
```

---

### Task 2: 访客人工事件 SSE 端点 `/human-events`

**Files:**
- Modify: `server/routes/kefu_public.py`（独立并发计数 + `/human-events` 端点）
- Modify: `server/tests/test_kefu_public_chat.py`（端点 404 + 计数上限用例）

**Interfaces:**
- Consumes: `kefu_event_bus.subscribe/unsubscribe`（Task 1）。
- Produces: `GET /kefu/sessions/<sid>/human-events?visitor_id=`（SSE）；`_human_sse_acquire(key)->bool` / `_human_sse_release(key)`。

- [ ] **Step 1: 写失败测试**

在 `server/tests/test_kefu_public_chat.py` 末尾追加（沿用其既有 import 与 fixtures；若无 `patch` 导入则在文件顶部加 `from unittest.mock import patch`）：

```python
def test_human_events_404_when_not_owner(client):
    with patch('routes.kefu_public.kefu_repo.load_kefu_session', return_value=None):
        resp = client.get('/kefu/sessions/sess_x/human-events?visitor_id=v1')
    assert resp.status_code == 404


def test_human_sse_cap_allows_two_then_blocks():
    from routes import kefu_public as kp
    kp._human_sse_active.clear()
    assert kp._human_sse_acquire('k') is True
    assert kp._human_sse_acquire('k') is True
    assert kp._human_sse_acquire('k') is False   # cap = 2
    kp._human_sse_release('k')
    assert kp._human_sse_acquire('k') is True
    kp._human_sse_active.clear()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_public_chat.py -v -k human`
Expected: FAIL（`AttributeError: _human_sse_acquire` / 端点 404 未实现返回其它）。

- [ ] **Step 3: 实现独立计数 + 端点**

在 `server/routes/kefu_public.py`：

(a) 在 SSE caps 常量区（`MAX_SSE_PER_IP = 6` 附近）加：

```python
MAX_HUMAN_SSE_PER_VISITOR = 2   # 人工事件流独立上限，不占 OpenCode 流配额
HUMAN_SSE_HEARTBEAT = 20        # 秒，心跳保活 + 探测断连
_human_sse_active: dict = {}
```

(b) 在 `_sse_release_ip` 之后加计数助手：

```python
def _human_sse_acquire(key: str) -> bool:
    with _sse_lock:
        count = _human_sse_active.get(key, 0)
        if count >= MAX_HUMAN_SSE_PER_VISITOR:
            return False
        _human_sse_active[key] = count + 1
        return True


def _human_sse_release(key: str) -> None:
    with _sse_lock:
        count = _human_sse_active.get(key, 0)
        if count <= 1:
            _human_sse_active.pop(key, None)
        else:
            _human_sse_active[key] = count - 1
```

(c) 顶部 import 段加（与其它 utils import 同处）：

```python
from utils import kefu_event_bus
import queue as _queue
```

(d) 在 `events()` 端点之后加新端点：

```python
@kefu_public_bp.route('/sessions/<sid>/human-events', methods=['GET'])
def human_events(sid):
    vid = (request.args.get('visitor_id') or '').strip()
    sess = kefu_repo.load_kefu_session(sid, vid)
    if not sess:
        return jsonify({'error': 'session not found'}), 404

    key = f"{sess[5]}:{vid}"
    if not _human_sse_acquire(key):
        return jsonify({'error': '并发连接过多，请稍后再试'}), 429

    q = kefu_event_bus.subscribe(sid)

    def generate():
        logger.info('kefu human-sse open session=%s', sid)
        try:
            yield _format_sse('ready', {})
            while True:
                try:
                    evt = q.get(timeout=HUMAN_SSE_HEARTBEAT)
                    yield _format_sse(evt.get('type', 'message'), evt)
                except _queue.Empty:
                    yield ': ping\n\n'
        except GeneratorExit:
            return
        except Exception:
            logger.exception('kefu human-sse error session=%s', sid)
            return
        finally:
            kefu_event_bus.unsubscribe(sid, q)
            _human_sse_release(key)
            logger.info('kefu human-sse closed session=%s', sid)

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
```

- [ ] **Step 4: 运行测试通过**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_kefu_public_chat.py -v -k human`
Expected: PASS（2 用例）。

- [ ] **Step 5: 全 kefu 后端回归**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/ -v -k kefu`
Expected: 全 PASS（含既有 takeover/public 用例）。

- [ ] **Step 6: Commit**

```bash
git add server/routes/kefu_public.py server/tests/test_kefu_public_chat.py
git commit -m "feat(kefu): 访客人工事件 SSE 端点 /human-events（独立并发上限）"
```

---

### Task 3: 访客前端 — 轮询改 SSE

**Files:**
- Modify: `src/api/kefuPublic.ts`（`createKefuHumanEventStream`）
- Modify: `src/views/kefu/KefuChatPage.vue`（删轮询、开人工事件流、`exitHumanMode`、release 复原）
- Modify: `src/views/kefu/__tests__/KefuChatPage.test.ts`（mock + 用例改造）

**Interfaces:**
- Consumes: 后端 `/human-events` SSE（Task 2）。
- Produces: `createKefuHumanEventStream(sid, { onReady?, onHumanMessage?, onTakeover?, onRelease? }): () => void`。

- [ ] **Step 1: 改测试（先失败）**

在 `src/views/kefu/__tests__/KefuChatPage.test.ts`：

(a) `vi.mock('@/api/kefuPublic', ...)` 对象里，`createKefuEventStream` 那行后加：

```typescript
  createKefuHumanEventStream: vi.fn(() => () => {}),
```

(b) 删除 B1 的两个轮询相关用例：`it('requestHuman enters humanMode and starts 3s polling', ...)` 和 `it('stops polling on unmount', ...)`（整段删除）。保留 `it('send that returns humanTakeover clears sending immediately + humanMode', ...)`。

(c) 追加新用例：

```typescript
  it('opens human-event stream on mount and closes on unmount', async () => {
    const close = vi.fn()
    vi.mocked(api.createKefuHumanEventStream).mockReturnValue(close)
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    expect(api.createKefuHumanEventStream).toHaveBeenCalledWith('sess_1', expect.any(Object))
    w.unmount()
    expect(close).toHaveBeenCalled()
  })

  it('onTakeover enters humanMode and reloads', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    const h = (vi.mocked(api.createKefuHumanEventStream).mock.calls.at(-1) as any)[1]
    vi.mocked(api.getKefuHistory).mockClear()
    h.onTakeover(); await flushPromises()
    expect((w.vm as any).humanMode).toBe(true)
    expect(api.getKefuHistory).toHaveBeenCalled()
  })

  it('onHumanMessage reloads history', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    const h = (vi.mocked(api.createKefuHumanEventStream).mock.calls.at(-1) as any)[1]
    vi.mocked(api.getKefuHistory).mockClear()
    h.onHumanMessage(); await flushPromises()
    expect(api.getKefuHistory).toHaveBeenCalled()
  })

  it('onRelease exits humanMode', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    await (w.vm as any).requestHuman()
    expect((w.vm as any).humanMode).toBe(true)
    const h = (vi.mocked(api.createKefuHumanEventStream).mock.calls.at(-1) as any)[1]
    h.onRelease(); await flushPromises()
    expect((w.vm as any).humanMode).toBe(false)
  })

  it('requestHuman enters humanMode', async () => {
    const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
    await flushPromises()
    await (w.vm as any).requestHuman()
    expect(api.requestHuman).toHaveBeenCalledWith('sess_1')
    expect((w.vm as any).humanMode).toBe(true)
  })
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts`
Expected: FAIL（`createKefuHumanEventStream` 未在组件调用 / `exitHumanMode` 未生效）。

- [ ] **Step 3: `kefuPublic.ts` 加 `createKefuHumanEventStream`**

在 `src/api/kefuPublic.ts` 的 `createKefuEventStream` 之后追加：

```typescript
export interface KefuHumanStreamHandlers {
  onReady?: () => void
  onHumanMessage?: () => void
  onTakeover?: () => void
  onRelease?: () => void
}

export function createKefuHumanEventStream(sid: string, h: KefuHumanStreamHandlers): () => void {
  const url = `/api/kefu/sessions/${encodeURIComponent(sid)}/human-events?visitor_id=${encodeURIComponent(getVisitorId())}`
  let es: EventSource | null = null, closed = false, attempt = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  const open = () => {
    if (closed) return
    es = new EventSource(url)
    es.addEventListener('ready', () => h.onReady?.())
    es.addEventListener('human_message', () => h.onHumanMessage?.())
    es.addEventListener('takeover', () => h.onTakeover?.())
    es.addEventListener('release', () => h.onRelease?.())
    es.onerror = () => {
      es?.close(); if (closed) return
      timer = setTimeout(open, RECONNECT_MS[Math.min(attempt, RECONNECT_MS.length - 1)]); attempt += 1
    }
    es.onopen = () => { attempt = 0 }
  }
  open()
  return () => { closed = true; if (timer) clearTimeout(timer); es?.close() }
}
```

（`RECONNECT_MS`、`getVisitorId` 已在文件内。）

- [ ] **Step 4: `KefuChatPage.vue` 改造**

(a) 删除轮询：删掉 `let pollTimer: ReturnType<typeof setInterval> | null = null`（第 93 行）、`function startPolling()...`（第 119 行）、`function stopPolling()...`（第 120 行）。

(b) 在 `let closeStream` 旁加人工流句柄——把
```typescript
let closeStream: (() => void) | null = null
```
改为
```typescript
let closeStream: (() => void) | null = null
let closeHumanStream: (() => void) | null = null
```

(c) `enterHumanMode` 去掉 `startPolling()`，并加 `exitHumanMode`——把
```typescript
function enterHumanMode() {
  if (humanMode.value) return
  humanMode.value = true
  sending.value = false
  startPolling()
}
```
改为
```typescript
function enterHumanMode() {
  if (humanMode.value) return
  humanMode.value = true
  sending.value = false
}
function exitHumanMode() { humanMode.value = false }
```

(d) `onMounted` 里，在 `closeStream = api.createKefuEventStream(...)` 那段之后、`} catch {` 之前加：
```typescript
    closeHumanStream = api.createKefuHumanEventStream(sessionId.value, {
      onReady: () => { reload().catch(() => {}) },
      onHumanMessage: () => { reload().catch(() => {}) },
      onTakeover: () => { enterHumanMode(); reload().catch(() => {}) },
      onRelease: () => { exitHumanMode(); reload().catch(() => {}) },
    })
```

(e) `onBeforeUnmount`——把
```typescript
onBeforeUnmount(() => { closeStream?.(); stopPolling() })
```
改为
```typescript
onBeforeUnmount(() => { closeStream?.(); closeHumanStream?.() })
```

- [ ] **Step 5: 运行测试通过**

Run: `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts`
Expected: PASS（现有保留用例 + 5 新/改用例全绿）。

- [ ] **Step 6: 类型检查**

Run: `npm run build`
Expected: vue-tsc 无错误（注意：`startPolling/stopPolling/pollTimer` 已全删，无 `noUnusedLocals` 报错）。

- [ ] **Step 7: Commit**

```bash
git add src/api/kefuPublic.ts src/views/kefu/KefuChatPage.vue src/views/kefu/__tests__/KefuChatPage.test.ts
git commit -m "feat(kefu): 访客侧人工事件改用 SSE（去轮询）+ 释放后复原"
```

---

### Task 4: 文档 + Playwright 实机验证

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`（第 10 节访客侧改「实时」）
- Evidence: `.playwright-mcp/` 截图

- [ ] **Step 1: 重启后端并确认服务**

后端改了代码——**重启** `python app.py`（:3002）使 `/human-events` 生效；确认前端 `:5173`（`npm run dev`）、OpenCode `:4096`。

- [ ] **Step 2: Playwright 走实时闭环**

1. 访客 `/kefu/demo` → 发一条消息 → 点「🙋 转人工」→ 断言提示条 + 「人工客服接入中」。
2. 网络面板断言存在 `human-events` SSE 连接、**无** 3s 轮询的 `GET .../messages`。
3. 管理端「人工接管」tab 接管该会话 → 断言访客页**即时**（≪3s）变化（若接管前访客已在接管态则观察人工回复即时性）。
4. 管理端人工回复「您好，人工客服」→ 断言访客页**即时**出现该气泡（不等 3s）。
5. 管理端**释放** → 断言访客页即时复原：提示条消失、状态回「在线」、「🙋 转人工」按钮回来。
6. 截图存 `.playwright-mcp/kefu-sse-realtime.png`；事后清理测试会话（`DELETE FROM ai_chat_sessions WHERE visitor_id='<测试id>'`）。

- [ ] **Step 3: 更新用户指南**

在 `docs/user-guide/ai/smart-customer-service.md` 第 10 节：把访客侧「每约 3 秒**轮询**」改为「经**独立 SSE 人工事件通道实时**接收人工回复/接管/释放」；补「管理员释放后访客页即时复原（提示条消失、可再次转人工）」。控制台实时仍标注属 C2（后续）。

- [ ] **Step 4: Commit（文档）**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 访客侧人工事件改为 SSE 实时"
```

---

## Self-Review

**Spec coverage:**
- 事件总线（subscribe/publish/unsubscribe + 有界丢弃）→ Task 1 Step 3 ✓
- 发布点（human_reply/takeover/release）→ Task 1 Step 7 ✓
- 访客 `/human-events` SSE + 独立并发上限 + 心跳 + finally 清理 → Task 2 Step 3 ✓
- `createKefuHumanEventStream` + KefuChatPage 轮询→SSE + exitHumanMode/release 复原 → Task 3 Step 3/4 ✓
- 重连补漏（`ready` 事件 → reload）→ Task 3（后端 `ready` 帧 + 前端 onReady→reload）✓
- 文档 + Playwright（含释放复原）→ Task 4 ✓

**Placeholder scan:** 无 TBD/TODO；每步含完整代码；测试含具体断言。✓

**Type consistency:**
- `subscribe/unsubscribe/publish`（Task 1）与 Task 2 端点、发布点用法一致 ✓
- `_human_sse_acquire/_release` + `_human_sse_active` + `MAX_HUMAN_SSE_PER_VISITOR`/`HUMAN_SSE_HEARTBEAT`（Task 2）命名一致 ✓
- `createKefuHumanEventStream(sid, {onReady,onHumanMessage,onTakeover,onRelease})`（Task 3 Step 3）与组件 onMounted 用法（Step 4d）、测试 mock 一致 ✓
- 事件 `type` 值 `takeover`/`release`/`human_message`（Task 1 发布）与前端 `addEventListener('takeover'|'release'|'human_message')`（Task 3）一致 ✓
- `enterHumanMode`/`exitHumanMode`/`closeHumanStream` 命名在组件、测试间一致 ✓
```
