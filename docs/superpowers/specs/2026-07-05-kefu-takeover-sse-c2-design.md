# 人工接管（Phase 2）子项目 C2：管理端控制台实时（SSE）设计

**日期**: 2026-07-05
**分支**: feat/kefu-self-service-faq
**范围**: 后端 + 管理前端。让 B2 管理端接管控制台订阅 C1 事件总线的**实例频道**，实时刷新队列与当前对话，取代 5s 轮询。管理端 SSE 用**短时一次性 ticket** 鉴权（EventSource 不能带 Authorization 头）。

## 背景

C1 已交付进程内事件总线（`kefu_event_bus`，`publish(key, event)`，key 为任意字符串）+ 访客 SSE 实时。B2 管理端控制台（`KefuTakeoverPanel`）仍靠 5s 轮询刷新队列与对话——管理员看到访客转人工/新消息有最长 5s 延迟。本子项目补上控制台实时。

关键约束：管理端 API 靠 JWT Bearer 头鉴权，但原生 `EventSource` 不能设置请求头。故 SSE 用**短时一次性 ticket**：先经已鉴权接口换 ticket，再用 `?ticket=` 开 SSE。token 不入 URL/日志，最安全。

## 目标

- 访客**转人工 / 发消息**、或任一管理员**接管 / 回复 / 释放** → 控制台队列与（若为当前打开会话）对话**实时**刷新，无需 5s 等待。
- 彻底去掉控制台 5s 轮询（`ready`/重连 reload 兜底）。

## 非目标（YAGNI）

- 不改 C1 事件总线（复用，实例频道用 key `inst:<iid>`）。
- 不改 B2 面板布局/交互（只把数据来源从轮询换成 SSE）。
- 不改后端状态机 / repo。
- 不做跨实例聚合、不做在线坐席列表、不做保留慢速安全轮询。

## 设计

### 1. 事件总线复用（无改动）

C1 的 `kefu_event_bus.publish(key, event)` key 是任意字符串。**实例频道**用 key `f"inst:{iid}"`，与会话频道（key = sid）互不干扰。事件形状 `{'sid': <sid>, 'type': <type>}`。

### 2. 发布点（在现有会话事件旁**新增**实例事件）

| 位置 | 现有 | 新增实例事件（key `inst:<iid>`） |
|---|---|---|
| `kefu_public.send_message` | 无 | `{'sid': sid, 'type': 'visitor_message'}`（iid = `sess[5]`） |
| `kefu_public.request_human` | 无 | `{'sid': sid, 'type': 'needs_human'}`（iid = `sess[5]`） |
| `kefu_admin.takeover_session_route` | 会话 `takeover` | + `{'sid': sid, 'type': 'takeover'}`（iid = `sess['kefu_instance_id']`） |
| `kefu_admin.release_session_route` | 会话 `release` | + `{'sid': sid, 'type': 'release'}` |
| `kefu_admin.human_reply` | 会话 `human_message` | + `{'sid': sid, 'type': 'human_message'}` |

（`send_message` 的实例事件在 user 消息入库后发布——无论是否接管态都发，供控制台看到访客消息。）

### 3. 短时 ticket `server/utils/kefu_sse_ticket.py`（新增）

进程内 dict + 锁，一次性 + TTL：

```python
import secrets, threading, time
_TTL = 60  # 秒
_tickets: dict[str, tuple] = {}   # ticket -> (user_id, expiry_ts)
_lock = threading.Lock()

def issue(user_id: str) -> str:
    t = secrets.token_urlsafe(24)
    with _lock:
        _tickets[t] = (user_id, time.time() + _TTL)
    return t

def consume(ticket: str) -> str | None:
    """一次性消费：命中且未过期→返回 user_id 并删除；否则 None。顺带清过期项。"""
    now = time.time()
    with _lock:
        expired = [k for k, (_, exp) in _tickets.items() if exp < now]
        for k in expired:
            _tickets.pop(k, None)
        item = _tickets.pop(ticket, None)
    if not item:
        return None
    user_id, exp = item
    return user_id if exp >= now else None
```

### 4. 管理端 SSE 端点（`server/routes/kefu_admin.py`）

- `POST /admin/kefu/events/ticket`（`@require_permission('admin.kefu')`）→ `kefu_sse_ticket.issue(g.current_user['userId'])` → `{'ticket': t}`。
- `GET /admin/kefu/events?instance=<iid>&ticket=<t>`（**无 `@require_permission`**，靠 ticket）：
  - `uid = kefu_sse_ticket.consume(ticket)`；`None` → 401。
  - `iid = request.args.get('instance')`；空 → 400。
  - 独立并发上限 `MAX_ADMIN_SSE_PER_USER = 3`（`_admin_sse_acquire(uid)/_release`，仿 kefu_public 的计数）；满 → 429。
  - 生成器：`subscribe(f"inst:{iid}")` → 首帧 `_format_sse('ready', {})` → 循环 `q.get(timeout=ADMIN_SSE_HEARTBEAT=20)` → 有事件 `yield _format_sse(evt['type'], evt)`（含 `sid`），空 → `: ping\n\n`；`GeneratorExit`/异常 → return；`finally` **必** `unsubscribe` + `_admin_sse_release(uid)`。
  - 响应头 `text/event-stream` + `Cache-Control: no-cache` + `X-Accel-Buffering: no`。
- `kefu_admin` 目前无 `_format_sse`——**在 `kefu_admin.py` 本地定义一份**（与 `kefu_public._format_sse` 同实现：`return f"event: {event}\ndata: {json.dumps(data)}\n\n"`），避免 routes 互相 import 的耦合。

### 5. 前端

**`src/api/kefu.ts`**：
```ts
export function requestSseTicket() { return post<{ ticket: string }>('/admin/kefu/events/ticket', {}) }
export function createKefuAdminEventStream(
  instanceId: string,
  handlers: { onReady?: () => void; onEvent?: (e: { sid?: string; type?: string }) => void }
): () => void
```
`createKefuAdminEventStream` 内部：**手动重连**（因 ticket 一次性）——`async open()`：`requestSseTicket()` → `new EventSource('/api/admin/kefu/events?instance=<iid>&ticket=<t>')` → 监听 `ready`（→onReady）、`visitor_message`/`needs_human`/`takeover`/`release`/`human_message`（→onEvent(parsed data)）；`onerror` → `es.close()` + 退避 `setTimeout(open, backoff)`；返回关闭函数（置 closed、清 timer、close）。事件 `data` 为 `{sid,type}` JSON，用 `JSON.parse(e.data)`。

**`src/components/kefu/KefuTakeoverPanel.vue`**：
- **删轮询**：移除 `queueTimer`/`convTimer` 及 `onMounted` 里的两个 `setInterval`。
- `onMounted` 开流：`closeStream = api.createKefuAdminEventStream(props.instanceId, { onReady: refresh, onEvent })`。
  - `refresh()` = `loadQueue().catch(()=>{})`（+ 选中则 `loadConversation().catch(()=>{})`）。
  - `onEvent(e)` = `loadQueue().catch(()=>{})`；若 `e.sid && e.sid === selectedSid.value` → `loadConversation().catch(()=>{})`。
- `watch(instanceId)`：清选中 + 关旧流 + 开新流（实例切换→换频道）。
- `onBeforeUnmount`：`closeStream?.()`。
- 手动「🔄 刷新」按钮保留（仍调 `loadQueue`）。

### 6. 数据流

控制台开 `inst:<iid>` SSE（onReady→loadQueue）。访客转人工 → `send_message`/`request_human` 发布 `inst:<iid>` → 控制台 `loadQueue`（队列冒出待人工）。管理员接管/回复/释放 → 发布 → 控制台 `loadQueue` + 命中当前会话则 `loadConversation`。访客发消息 → 控制台若正看该会话则对话实时更新。

## 错误处理

- ticket 缺失/过期/已用 → 401；前端 `onerror` → 退避后重取 ticket 重连。
- SSE 生成器异常/`GeneratorExit` → `finally` 必 `unsubscribe` + 释放并发位。
- 并发超限 → 429（前端退避重试）。
- 发布失败不影响主流程（`publish` 内部 `put_nowait` 满即丢）。
- **容量**：管理端长连接占 waitress 线程，但管理员数远小于访客；`MAX_ADMIN_SSE_PER_USER=3` 兜底。

## 组件边界与文件

| 文件 | 职责 | 变更 |
|---|---|---|
| `server/utils/kefu_sse_ticket.py` | 短时一次性 ticket | **新增** |
| `server/routes/kefu_admin.py` | ticket 端点 + `/events` SSE + 3 处实例发布 + 并发计数 | 改 |
| `server/routes/kefu_public.py` | send_message / request_human 实例发布 | 改（各 +1 行） |
| `src/api/kefu.ts` | `requestSseTicket` + `createKefuAdminEventStream` | 改 |
| `src/components/kefu/KefuTakeoverPanel.vue` | 轮询→SSE | 改 |

## 测试

**后端**（`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）：
- **新增** `server/tests/test_kefu_sse_ticket.py`：`issue` 返回 token 且可 `consume` 得 user_id；`consume` 一次性（第二次 None）；过期 ticket → None（用极小 TTL 或 monkeypatch `time.time`）；未知 ticket → None。
- **扩展** `server/tests/test_kefu_takeover_admin.py`：ticket 端点需 admin.kefu（dev→403）、返回 ticket；`/events` 无/坏 ticket → 401；takeover/release/human_reply 除会话事件外**也** publish `inst:<iid>` 实例事件（断言 publish 以 `f"inst:{iid}"` 被调）。
- **扩展** `server/tests/test_kefu_public_chat.py`：`send_message` 发布实例 `visitor_message`；`request_human` 发布实例 `needs_human`（patch `kefu_public.kefu_event_bus.publish`，断言含 `inst:` key 的调用）。
- 全 kefu 后端回归全绿。

**前端**（`src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`，mock `@/api/kefu` 补 `createKefuAdminEventStream`/`requestSseTicket`）：
- 挂载 → `createKefuAdminEventStream(instanceId, handlers)` 被调；卸载 → 返回的关闭函数被调。
- `onReady` → `listSessions` 被调（刷新队列）。
- `onEvent({sid:'other'})` → `listSessions` 被调、`getSessionMessages` **不**被调（非当前会话）。
- 先 `selectSession('sess_1')`，再 `onEvent({sid:'sess_1'})` → `getSessionMessages` 被调（当前会话对话刷新）。
- 不再有基于 `setInterval` 的轮询（推进定时器 `listSessions` 不因计时器增调）。
- 现有用例保持全绿；`npm run build` 无错误。

## 验证（MANDATORY）

Playwright（前端 :5173 + 后端 :3002 + OpenCode :4096，**后端需重启**）：
1. 管理端登录 → 智能客服 → 选「演示客服」→「人工接管」tab（此时开启 admin SSE）。
2. **另一浏览器上下文/标签**访客 `/kefu/demo` → 点「🙋 转人工」。
3. 断言控制台队列**实时**（≪5s、无需手动刷新）冒出该待人工会话。
4. 控制台点该会话接管；访客发一条消息 → 断言控制台对话区**实时**出现该访客消息。
5. 网络面板断言存在 `admin/kefu/events` SSE、**无** 5s 轮询的 `GET /admin/kefu/sessions`。
6. 截图存 `.playwright-mcp/`；事后清理测试会话。

## 文档同步

`docs/user-guide/ai/smart-customer-service.md` 第 10 节：把管理端控制台「5s 自动刷新」更新为「SSE 实时（订阅事件总线）」；标注人工接管 Phase 2 前端全链路已全实时。
