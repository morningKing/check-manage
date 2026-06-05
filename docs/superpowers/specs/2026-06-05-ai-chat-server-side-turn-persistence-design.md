# AI Chat：服务端独立持久化（切会话不丢中间态）

**Date:** 2026-06-05
**Status:** Approved (design)

## 问题背景

会话流式输出过程中（模型尚未输出完成、工具调用记录与中间态文本只存在于前端内存），用户切换到其他会话/窗口后，这些记录消失且**无法找回**。

根因（已在代码中确认）：

1. **持久化绑定在浏览器连接上。** 助手消息只在后端 SSE 代理 `sse_events`（`server/routes/ai_chat.py:441-499`）的逐连接生成器里，于 `session.idle` 时写入 `ai_chat_messages`（`:486-490`）。累积状态（`parts_by_id`/`part_order`）是该连接私有的。
2. **切会话即断开。** 前端只有一个共享 SSE（`_stream`，`src/stores/aiChat.ts:51`），`openSession` 切换时 `_openStream` 先 `_closeStream()`（`:335-347`），关闭了正在流式的那个会话的连接。后端生成器收到 `GeneratorExit` 直接 `return`（`routes/ai_chat.py:498`），**丢弃累积的 parts**。
3. **无人持久化 = 永久丢失。** OpenCode 仍在后台生成，但每个生成器只过滤自己会话的事件（`:457-459`），被关闭的会话无人接收其 `session.idle`，该助手消息**从不入库**。切回时 `getMessages` 从库里读，自然没有；前端内存里的 `streaming_*` 临时消息也被 `getMessages` 覆盖（`src/stores/aiChat.ts:133`）。

约束：OpenCode 的 `/event` 必须按 `?directory=`（每会话工作区）订阅；无 directory 的全局流只有心跳（`server/utils/opencode_client.py:139-151`）。因此**无法用单个全局监听器**覆盖所有会话，持久化必须按活跃会话驱动。

## 目标 / 非目标

- **目标（用户选定）：** 保证助手回复（含工具调用）不丢失、可找回 —— 后端独立于浏览器持久化每个会话的回合。切走后回合仍会完成并入库，切回能看到完整记录。
- **非目标：** 不要求后台会话在前端“实时”继续更新；不要求切回时立刻看到“生成一半”的中间态（那是更大的增量持久化方案，本次不做）。不改 OpenCode；不重构前端单流模型。

## 架构

### A. 新单元 `server/utils/chat_persist.py`

一个按会话 id 管理守护线程的小型管理器（加锁，参照 `batch_engine` / `ai_scan` 模式）：

- `ensure_listener(sid, opencode_session_id, directory)`：若该会话无运行中的监听线程则启动一个；已存在则无操作（去重）。
- 监听线程：通过 `OpenCodeClient(OPENCODE_BASE_URL).subscribe_events(directory)` 订阅该会话事件流，用共享累积逻辑累积 parts，在**每次** `session.idle` 时持久化助手消息并重置累积，继续监听后续回合。
- 退出条件：不活跃超时（约 30 分钟无事件）、连续连接错误（少量重试后放弃）、或会话被删除。退出时从管理器字典移除自己。

### B. 共享、可测的累积逻辑

把目前内联在 `sse_events`（`routes/ai_chat.py:448-495`）的 part 累积逻辑抽成纯函数，供监听器与代理共用，且可脱离 OpenCode 单测：

```
new_state() -> state                      # {'assistant_msg_ids', 'parts_by_id', 'part_order', 'turn_msg_id'}
apply_event(state, evt) -> 'idle' | None  # 消费一个 {'event','data'}；返回 'idle' 表示回合结束
build_content(state) -> list              # 复用现有 _build_assistant_content 的产物
```

`apply_event` 处理 `message.updated`（记录 assistant 消息 id，并把**首个** assistant 消息 id 存为该回合的 `turn_msg_id`）、`message.part.updated`（text / tool → 累积）、`session.idle`（返回 `'idle'`）。`sse_events` 改为调用这些 helper，行为不变。

### C. 幂等持久化（纵深防御，避免回归）

持久化以**回合的 OpenCode 助手消息 id**（`turn_msg_id`，确定性）作为行 id，幂等 upsert：

```sql
INSERT INTO ai_chat_messages (id, session_id, role, content)
VALUES (%s, %s, 'assistant', %s)
ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content
```

于是**保留代理原有的 idle 持久化作为安全网**，新监听器保证无浏览器时也持久化 —— 谁先看到 `session.idle` 谁写，二者收敛到同一行：**不产生重复行、不回归**。回合只要被任一方观测到结束即入库。

> 若某回合拿不到 `turn_msg_id`（异常/旧事件），回退为现有的 `'msg_' + secrets.token_hex(6)` 生成式 id（非幂等，但不阻塞流程）。

### D. 接线

在“开启回合”的端点里调用 `ensure_listener(...)`：

- `send_message`（POST `/sessions/<sid>/messages`，`routes/ai_chat.py`）。
- `run_session_command`（POST `/sessions/<sid>/command`）。

（retry 走 `send_message`，无需单独接线。）浏览器 SSE 代理其余逻辑不变，仍负责把事件实时转发给前端。

### E. 生命周期与限制

- 不活跃超时（约 30 分钟）释放空闲监听线程；下个回合再 `ensure_listener` 重启。
- OpenCode 连接瞬断：少量重试后放弃；放弃时**不**写入残缺内容（避免半截入库被当成最终态）。
- 会话被删除（`closeSession`/`deleteSession`）时停止其监听线程。
- **已知限制：** Flask 进程在回合进行中重启，该在途回合不恢复（与现有 backup/dependency/scan 调度器同样的取舍），文档说明，不处理。

## 数据流（修复后）

```
用户发消息 → POST /messages
  → OpenCode 开始生成
  → ensure_listener(sid) 启动后台守护线程（若未运行）
  → 浏览器 SSE 代理同时订阅，实时转发给前端（live 渲染）
用户切到别的会话 → 前端关闭该会话 SSE（代理生成器退出）
  → 后台监听线程仍在订阅、累积
  → 回合结束 session.idle → 后台线程幂等 upsert 助手消息入库
用户切回 → getMessages 从库读到完整助手消息（含工具调用）→ 正常显示
```

## 错误处理

- DB 异常：upsert 包 try/except，失败不打断监听（与现有 `_persist_assistant_message` 容错一致）。
- OpenCode 不可用 / 订阅异常：重试有限次后线程退出；不写残缺态。
- 重复 `ensure_listener`：管理器加锁去重，单会话仅一个线程。

## 测试

- **纯函数累积**（`server/tests/test_chat_persist.py`）：`apply_event` 序列（text/tool 累积、首个 assistant id 记为 turn_msg_id、`session.idle` 返回 `'idle'`、按到达顺序）→ `build_content` 产物正确；空文本被丢弃、工具 part 保留。
- **幂等 upsert**：同一 `turn_msg_id` 持久化两次 → 只一行、content 被更新（用真实临时库或 mock cursor 断言 SQL/参数）。
- **`ensure_listener` 去重**：对同一会话连续调用 → 只启动一个线程（mock 线程目标 / 用事件可控的假 `subscribe_events`）。
- **路由接线**：`send_message` / `run_session_command` 调用 `ensure_listener`（patch 断言被调用且参数为会话工作区）。
- 事件迭代器全程 mock，无需真实 OpenCode。

## 涉及文件

- 新增：`server/utils/chat_persist.py`（管理器 + 监听线程 + `new_state`/`apply_event`/`build_content` + 幂等 upsert）
- 新增：`server/tests/test_chat_persist.py`
- 修改：`server/routes/ai_chat.py`（`sse_events` 改用共享 helper；`send_message`/`run_session_command` 调用 `ensure_listener`；持久化改幂等 upsert；删除会话时停止监听）
- 可能修改：`server/routes/ai_chat.py` 的 `_build_assistant_content`/`_persist_assistant_message` 迁入或复用自 `chat_persist`
