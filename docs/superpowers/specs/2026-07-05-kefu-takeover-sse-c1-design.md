# 人工接管（Phase 2）子项目 C1：事件总线 + 访客实时（SSE 实时合并）设计

**日期**: 2026-07-05
**分支**: feat/kefu-self-service-faq
**范围**: 后端 + 访客前端。建进程内事件总线，在人工回复/接管/释放处发布事件；访客页新增一条独立「人工事件」SSE 通道消费它，取代 B1 的 3s 轮询，实现人工回复/接管/释放的**实时**呈现。管理端控制台实时（订阅同一总线）属子项目 **C2**。

## 背景

人工接管后端（A）、访客转人工+轮询（B1）、管理端控制台+轮询（B2）已上线。访客侧现靠 3s 轮询取人工回复；管理员接管/释放对访客无实时反馈，且 B1 一旦进入人工态**永不复原**（final review 记录的遗留）。访客 SSE（`GET /kefu/sessions/:id/events`）阻塞迭代 OpenCode 事件流，接管期间 OpenCode 空转、发不出人工事件——故需一条**独立**的人工事件通道。

关键前提：生产为 **waitress 单进程有界线程池**（`BACKEND_THREADS`），开发为 Werkzeug threaded——均单进程，故**进程内 pub/sub 即可，无需 Redis**。代码库已有同类模式（`chat_persist._listeners` dict + `threading.Lock` 的按会话后台线程）。

## 目标

- 访客**实时**收到人工回复（不再等 3s 轮询）。
- 管理员**接管**即时反映到访客页（提示条 + 「人工客服接入中」），无需访客先发消息。
- 管理员**释放**即时让访客页复原（提示条消失、状态回「在线」、「🙋 转人工」按钮回来）——修 B1 遗留。

## 非目标（YAGNI）

- 不改 OpenCode 代理 SSE（`events()`）——隔离，避免影响正常 AI 对话与 B1。
- 不改后端状态机 / repo 语义。
- 事件**只做信号**，不携带消息体（客户端收到信号后 `reload()` 拉历史）——避免去重/顺序复杂度。
- 不做管理端控制台实时（属 C2，其 5s 轮询本阶段保留）。
- 不做跨进程/持久化事件（进程重启期间的瞬时事件丢失可接受，重连 `onopen` reload 兜底）。

## 设计

### 1. 事件总线 `server/utils/kefu_event_bus.py`（新增）

进程内按 `session_id` 分发的 pub/sub：

```python
import queue, threading
_subscribers: dict[str, set[queue.Queue]] = {}
_lock = threading.Lock()
_MAX_Q = 100   # 每订阅者有界队列，防内存膨胀

def subscribe(sid: str) -> queue.Queue:
    q = queue.Queue(maxsize=_MAX_Q)
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

事件形状：`{'type': 'human_message' | 'takeover' | 'release'}`（C1 只需这三种）。

### 2. 发布点（`server/routes/kefu_admin.py`，路由处发布，repo 保持纯）

- `human_reply`：`insert_human_message(...)` 成功后 → `kefu_event_bus.publish(sid, {'type': 'human_message'})`。
- `takeover_session_route`：`takeover_session(...)` 后 → `publish(sid, {'type': 'takeover'})`。
- `release_session_route`：`release_session(...)` 后 → `publish(sid, {'type': 'release'})`。

### 3. 访客人工事件 SSE `GET /kefu/sessions/<sid>/human-events`（`server/routes/kefu_public.py`）

- 查询参数 `visitor_id`；`kefu_repo.load_kefu_session(sid, vid)` 归属校验，失败 404（同 `events()`）。
- **独立并发上限**：新增 `MAX_HUMAN_SSE_PER_VISITOR = 2`，独立计数器（`_human_sse_acquire/_release`，仿现有 `_sse_acquire`），**不占** OpenCode 流的 `MAX_SSE_PER_VISITOR` 配额；满则 429。
- 生成器：
  ```
  q = kefu_event_bus.subscribe(sid)
  try:
      yield _format_sse('ready', {})           # 首帧，触发前端 onopen
      while True:
          try:
              evt = q.get(timeout=HUMAN_SSE_HEARTBEAT)   # 20s
              yield _format_sse(evt['type'], evt)
          except queue.Empty:
              yield ': ping\n\n'                # 心跳保活 + 探测断连
  except GeneratorExit:
      return
  finally:
      kefu_event_bus.unsubscribe(sid, q)
      _human_sse_release(...)
  ```
- 响应头同 `events()`：`text/event-stream` + `Cache-Control: no-cache` + `X-Accel-Buffering: no`。

### 4. 访客前端

**`src/api/kefuPublic.ts`**：新增（仿现有 `createKefuEventStream`）
```ts
export function createKefuHumanEventStream(
  sid: string,
  handlers: { onReady?: () => void; onHumanMessage?: () => void; onTakeover?: () => void; onRelease?: () => void }
): () => void
```
用 `EventSource('/api/kefu/sessions/<sid>/human-events?visitor_id=<vid>')`，`addEventListener('ready'|'human_message'|'takeover'|'release', ...)`，返回关闭函数。

**`src/views/kefu/KefuChatPage.vue`**：
- **删除轮询**：移除 `startPolling`/`stopPolling`/`pollTimer`。
- 保留 `humanMode`/`enterHumanMode`；新增 `exitHumanMode()`（`humanMode.value=false`）。
- `onMounted`（session 建好后）开人工事件流并存到 `closeHumanStream`：
  - `onReady` / 每次重连 → `reload()`（补断连期间漏的人工消息）。
  - `onTakeover` → `enterHumanMode()` + `reload()`。
  - `onHumanMessage` → `reload()`。
  - `onRelease` → `exitHumanMode()` + `reload()`。
- `enterHumanMode()` 去掉 `startPolling()`（改由 SSE 驱动）。
- `requestHuman()` 点击仍即时 `enterHumanMode()`；`send()` 收 `humanTakeover:true` 仍即时 `enterHumanMode()`（双保险）。
- `onBeforeUnmount`：`closeHumanStream?.()`（与现有 `closeStream?.()` 并列）。

### 5. 数据流

访客开 human-events SSE（onopen→reload）。管理员接管 → 路由 `publish(takeover)` → 总线投递 → 访客 SSE 收 `takeover` → enterHumanMode+reload（**即时**）。人工回复 → `publish(human_message)` → 访客 `reload()`（**即时**，非 3s）。释放 → `publish(release)` → 访客 exitHumanMode+reload（**即时复原**）。

## 错误处理

- 总线 `put_nowait` 满 → 丢弃（信号型，无害）。
- SSE 生成器异常/`GeneratorExit` → `finally` 必 `unsubscribe` + 释放配额（防泄漏）。
- 前端 `EventSource.onerror` → 浏览器自动重连；重连后 `ready`/`onopen` reload 补漏。不额外报错打扰访客。
- 并发超限 → 429（前端静默，不影响已有 OpenCode 流与聊天）。

**容量/部署注意**：SSE 为长连接，每条占用一个 waitress 线程整个生命周期。本通道使每访客标签页多占 1 条连接（OpenCode 流 + 人工事件流）。已有 `MAX_SSE_PER_IP=6` + 新 `MAX_HUMAN_SSE_PER_VISITOR=2` 做每客户端上限，但不设全局上限——自托管低流量客服场景可接受；若并发访客数逼近 `BACKEND_THREADS`，需上调线程数或引入异步网关（属后续，不在 C1）。

## 组件边界与文件

| 文件 | 职责 | 变更 |
|---|---|---|
| `server/utils/kefu_event_bus.py` | 进程内 pub/sub | **新增** |
| `server/routes/kefu_admin.py` | human_reply/takeover/release 发布事件 | 改（各 +1 行） |
| `server/routes/kefu_public.py` | human-events SSE 端点 + 独立并发计数 | 改 |
| `src/api/kefuPublic.ts` | `createKefuHumanEventStream` | 改 |
| `src/views/kefu/KefuChatPage.vue` | 轮询→SSE、exitHumanMode、release 复原 | 改 |

## 测试

**后端**（`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）：
- **新增** `server/tests/test_kefu_event_bus.py`：subscribe 返回队列并注册；publish 投递给该 sid 所有订阅者、不投递给别的 sid；两个订阅者都收到；队列满时 `put_nowait` 不抛（被吞）；unsubscribe 移除、空 sid 清理。
- **扩展** `server/tests/test_kefu_takeover_admin.py`（patch `routes.kefu_admin.kefu_event_bus.publish`）：`takeover`/`release`/human `reply` 各路由成功后以正确 `sid` + `type` 调 `publish`。

**前端**（`src/views/kefu/__tests__/KefuChatPage.test.ts`，mock `@/api/kefuPublic` 补 `createKefuHumanEventStream`）：
- 挂载后 `createKefuHumanEventStream` 被调（sid 正确），返回的关闭函数在卸载时被调。
- `onTakeover` 回调 → `humanMode=true` 且 `getKefuHistory` 再调。
- `onHumanMessage` → `getKefuHistory` 再调。
- `onRelease` → `humanMode=false`。
- 不再有基于 `setInterval` 的轮询（原「polls…」用例改为「不轮询」：推进定时器 `getKefuHistory` 不因计时器增调）。
- 现有用例保持全绿；`npm run build`（vue-tsc）无错误。

## 验证（MANDATORY）

Playwright（前端 :5173 + 后端 :3002 + OpenCode :4096）：
1. 访客 `/kefu/demo` → 点「🙋 转人工」→ 提示条 + 「人工客服接入中」。
2. 管理端「人工接管」tab 接管该会话 + 人工回复。
3. 断言访客页**≈即时**（≪3s，不等轮询）出现人工回复气泡；网络面板确认 `human-events` SSE 连接存在、无 3s 轮询 `messages` 请求。
4. 管理端**释放** → 断言访客页即时复原（提示条消失、状态「在线」、「转人工」按钮回来）。
5. 截图存 `.playwright-mcp/`；事后清理测试会话。

## 文档同步

`docs/user-guide/ai/smart-customer-service.md` 第 10 节：把访客侧「靠轮询」更新为「实时（SSE 人工事件通道）」，补释放后访客复原说明；标注控制台实时属 C2（后续）。
