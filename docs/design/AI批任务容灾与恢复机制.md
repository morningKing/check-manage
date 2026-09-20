# AI 批任务容灾与恢复机制

> **迁移说明**：专题细节已吸收到 [`09-AI智能助手.md`](./09-AI智能助手.md)；本文保留专题背景/实施细节；实现状态、表结构、接口以 09 和当前代码为准。

> 适用范围：AI 批任务（`ai_chat_batches`）、AI 扫描任务（`ai_scan_tasks`）与开放
> API 子会话在 **OpenCode serve 重启**或**服务端进程/整机崩溃**后的自动恢复。
> 代码基线：`server/utils/batch_engine.py`、`server/utils/ai_scan_engine.py`、
> `server/utils/ai_scan_scheduler.py`、`server/app.py`（启动装配）。

---

## 1. 结论

**有兜底，且是成体系的三层防线 + 一个记录级兜底。** 批任务的全部状态都在数据库里，
调度是"从 DB 认领工作"的模式：无论是 opencode 重启还是整机崩溃，系统恢复后约
10 秒内，运行中对账器会把崩溃残留的 `running` 残账按实际状态分流——能续跑的原地
续跑，会话真丢的带准确原因失败，OpenCode 不可达时不误杀。pending 任务由调度器
继续正常推进，业务记录侧（扫描任务）由孤儿清扫回滚重试。

```
崩溃/重启
   │
   ▼
┌──────────────────────────────────────────────────────────────────┐
│ DB 即账本：ai_chat_sessions 的 pending/running/retry_count/       │
│ continue_prompt 全部持久化；dispatcher 每轮 FOR UPDATE SKIP       │
│ LOCKED 认领，内存清空不影响任务定义与状态                         │
└──────────────┬───────────────────────────────────────────────────┘
               ▼
  防线一（主兜底）：运行中对账器 _reconcile_stale_running
  running 且不属于本进程任何线程的残账，与 OpenCode 对齐分流：
    · 无 oc id           → 整体重排（全新执行）
    · 会话 404（丢失）   → 带准确原因失败
    · OpenCode 不可达    → 跳过本轮，不批量误杀
    · 会话还活着         → 原地续跑（预算内），从原上下文继续
               ▼
  防线二：OpenCode 会话失效的派发期恢复 _recover_session
  新建 oc 会话 + 注入历史摘要 + 重发（与交互聊天 M3 同机制）
               ▼
  防线三：失败分类自动重试 + 无人值守防呆三件套
  可重试白名单 / 停滞·工具卡死看门狗 / question 自动拒绝
               ▼
  记录级兜底（扫描任务）：sweep_orphans 每分钟回滚无主记录 → 下轮重试
```

---

## 2. 基础：状态在库里，不在内存里

调度器每轮用 `FOR UPDATE SKIP LOCKED` 从 `ai_chat_sessions` 认领 `pending`
子任务（`_claim_pending_sessions`，batch_engine.py:703），一次认领一批并交
线程池执行。进程重启后内存（线程、`_running_session_ids` 集合）清空，但：

- 任务定义、顺序、输入都在 `ai_chat_sessions` / `ai_chat_batches`；
- 运行状态、重试计数（`retry_count`）、续跑提示词（`continue_prompt`）、
  取消/暂停标志（`cancel_requested` / `pause_requested`）都在行内；
- dispatcher 由 `app.py:243` 在服务启动时拉起，继续按 claim 语义调度。

因此"系统恢复正常后任务继续"不需要显式的恢复指令——调度循环起来，账本里的
任务自然被重新处理；唯一需要 special 处理的是崩溃瞬间正在 `running` 的行，
这正是防线一的职责。

---

## 3. 防线一：运行中对账器（崩溃残账的主兜底）

`_reconcile_stale_running`（batch_engine.py:1776），由 dispatcher 主循环驱动，
默认每 **60 秒**一轮（`AI_BATCH_RECONCILE_SEC`），进程启动后首轮约 **10 秒内**
执行（batch_engine.py:629-646）。与 dispatcher 同线程运行，天然避开
claim→入账窗口的竞态；任何异常只记日志，绝不影响调度主循环。

抽查对象：`status='running'` 且 `batch_id` 或 `api_key_id` 非空、**但不在本进程
线程登记表里**的行——崩溃重启后所有孤儿残账都命中这个条件。逐行与 OpenCode
实际状态对齐：

| 对账发现 | 处理 | 理由 |
|----------|------|------|
| 子任务还没有 opencode 会话 id（崩溃发生在 create_session 之前） | `_requeue_lost(continue_on_same=False)`：重排为全新执行 | 没有可续的上下文 |
| `get_messages` 返回 404（会话被清理/丢失） | 带准确原因失败："对账器发现 OpenCode 会话已失效（服务端可能重启或清理过该会话）" | 续跑无门，明确终态好过误导性的"无进展" |
| OpenCode 整体不可达（连接拒绝/超时） | **跳过本轮** | serve 可能只是暂时下线，不能把一批运行中任务全部误杀 |
| 会话活着，只是没人轮询了 | `_requeue_lost(continue_on_same=True)`：回 `pending`、`retry_count+1`、写入 `AUTO_RETRY_CONTINUE_PROMPT`，重新排队后以 **continue 模式**复用原 opencode 会话的完整上下文续跑 | 任务进度在 OpenCode 会话里持久着，续跑代价最小 |

`_requeue_lost`（batch_engine.py:1827）消耗重试预算 `retry_count`，超过
`AI_BATCH_MAX_AUTO_RETRY`（默认 2）才落 `failed`（"自动重试预算已用尽"）——
既防止无限循环，也保证终态明确、可手动"重试失败项/继续执行"。

自动续跑注入的提示词（`AUTO_RETRY_CONTINUE_PROMPT`，batch_engine.py:575）：

> 上一轮执行因异常中断（系统已自动重试）。请先查看工作区现状与已有对话上下文，
> 从中断处继续完成原任务；已完成的部分不要重做，最终给出完整结果。

---

## 4. 防线二：派发期会话失效恢复（_recover_session）

防线一处理"已经派发出去的回合"；这条防线处理"派发动作本身撞上 serve 重启"：
`send_message` 失败时 `_recover_session`（batch_engine.py:1239）执行——

1. 新建 OpenCode 会话；
2. 用 `session_history.render_history_block` 渲染历史摘要；
3. 以「历史摘要 + 原提示词」重发到新会话；
4. 更新子任务的 `opencode_session_id` 绑定。

与交互式聊天的 `_recover_session_and_resend`（routes/ai_chat.py，M3 机制）是
同一套语义，共用历史渲染工具。恢复动作本身失败会带上真实原因落 `failed`，
派发失败的原因在日志里不会丢失。

> 注：OpenCode 的会话数据是落盘持久的——serve 重启后原会话通常仍可读
> （对账器按"活着"处理，走原地续跑）；404 只发生在会话被清理等场景。
> 两条恢复路径覆盖两种可能。

---

## 5. 防线三：失败分类自动重试 + 无人值守防呆

### 5.1 自动重试（失败不终局）

| 配置 | 默认 | 说明 |
|------|------|------|
| `AI_BATCH_MAX_AUTO_RETRY` | 2 | 可重试失败的自动重排预算；运行失败与对账重排**共用**同一计数；0 = 关闭 |
| `RETRYABLE_TURN_FAILED` | `APIError`、`UnknownError`、`MessageOutputLengthError`、`StructuredOutputError` | 值得重试的 provider 错误白名单 |

刻意**不**重试：`ProviderAuthError`（密钥错，重试必然再炸）、用户主动中断
（`MessageAbortedError`）、上下文超限（`ContextOverflowError`，续跑只会更大）。
停滞（`stalled (no progress)`）与工具卡死（`tool stuck`）产生的失败也计入可重试。

### 5.2 无人值守防呆三件套（挂起防线）

批任务没有用户盯梢，挂起等于永久卡死，因此有三道看门狗：

1. **question 自动拒绝**：模型用 question 工具向用户提问会无限期挂起回合；
   每 10 秒检查一次，发现 pending 问题自动拒绝（等价于用户点"拒绝"），模型继续。
2. **工具卡死看门狗**：`AI_BATCH_TOOL_STALL_SEC`（默认 900 秒）内某工具调用的
   (name, status, output) 签名完全冻结、且没有活跃子代理作保 → abort + 失败
   （`tool stuck`）。子代理委托（task 工具）不受此限：子代理自己的消息在推进
   就一直顺延。
3. **整体停滞看门狗**：180 秒内既无新文本也无工具在跑 → abort + 失败
   （`stalled (no progress)`）。

另有可选的会话硬超时 `AI_BATCH_SESSION_TIMEOUT_SEC`（默认 0 = 不设硬上限）。
以上失败均进入 5.1 的自动重试预算。

---

## 6. 记录级兜底：扫描任务的孤儿清扫

AI 扫描任务（含批处理触发的扫描、定时扫描、开放 API 动作）比会话多一层
**业务记录级**的对账：`sweep_orphans`（ai_scan_engine.py:448，调度器
`ai_scan_scheduler` 每 1 分钟执行一次）：

- 扫描认领记录时原子地把业务记录的状态字段翻到 running 值；
- `sweep_orphans` 把**没有对应活会话（pending/running 子会话）**的 running
  记录重置回 pending 值——崩溃后"记录卡在处理中"的死状态在一分钟内自愈；
- 下一轮扫描（手动或定时）自动重新认领这些记录重试；
- 认领后中途失败同样把已认领记录回滚 pending（ai_scan_engine.py:422）。

记录级状态翻转是原子的，所以这一层是**强幂等**的：重试不会重复处理同一条记录。

---

## 7. 场景走查

### 7.1 仅 OpenCode serve 重启

1. 运行中任务线程的 REST 轮询报连接错误；
2. serve 恢复后：OpenCode 会话数据落盘持久，对账器/轮询能重新读到会话 →
   走**原地续跑**（continue 模式，原上下文）；
3. 会话真被清理（404）→ 对账器带准确原因失败（可手动重试）；
4. 崩溃恰好发生在派发瞬间 → `_recover_session` 换新会话 + 历史摘要续跑。

### 7.2 服务端进程 / 整机崩溃

1. 工作线程全部消失，DB 留下 `running` 残账与未认领的 `pending` 任务；
2. 系统恢复、BKB 后端启动 → dispatcher 启动（app.py:243）；
3. 约 10 秒内首轮对账：残账按防线一分流（多数走原地续跑）；
4. `pending` 任务被正常认领，批任务状态由 `_recompute_batch_status` 依据
   子任务计数重算，通知回调（`_notify_callback`）在批次回到运行/终态时补发；
5. 扫描任务侧：孤儿清扫每分钟把无主记录回滚 pending，定时扫描下一轮重试。

---

## 8. 边界与调优建议

### 8.1 续跑不是强幂等（会话层）

会话级续跑靠提示词约束"已完成的部分不要重做"——模型通常遵守，但没有断点
续算机制：反复中断的同一子任务在续跑时可能重复产生一些文件写入。对结果的
影响一般是覆盖写同路径文件；如果任务语义对重复写入敏感，建议在提示词模板里
要求"先检查产物是否已存在"。**扫描任务的记录级重试没有这个问题**（原子认领）。

### 8.2 重试预算是共享的

运行失败自动重试与对账重排共用 `AI_BATCH_MAX_AUTO_RETRY`。部署环境若崩溃
频繁（如开发机），预算 2 次可能提前耗尽 → 子任务落 failed（终态）。两种应对：
调大环境变量；或失败后使用批任务面板的「重试失败项 / 继续执行」（会把批次
拉回运行态，`_notify_callback` 的注释说明了这一语义）。

### 8.3 交互式会话不在 dispatcher 管辖

本文机制覆盖批任务/扫描/开放 API 子会话。交互式聊天的中断恢复由用户可见的
"重试/继续"与 M3 会话重建机制承担，不在自动对账范围。

---

## 9. 环境变量总表

| 变量 | 默认 | 说明 |
|------|------|------|
| `AI_BATCH_MAX_AUTO_RETRY` | `2` | 可重试失败的自动重排预算（运行失败与对账重排共用）；0 = 关闭 |
| `AI_BATCH_RECONCILE_SEC` | `60` | 运行中对账器轮询间隔（秒） |
| `AI_BATCH_TOOL_STALL_SEC` | `900` | 工具卡死看门狗阈值（秒）；0 = 关闭（不建议） |
| `AI_BATCH_SESSION_TIMEOUT_SEC` | `0` | 单子任务会话硬超时（秒）；0 = 不设硬上限 |
| `AI_BATCH_CONCURRENCY` 等 | — | 并发度运行时可配（管理界面），非崩溃恢复范畴 |

（停滞看门狗 180s 与 question 检查 10s 为代码内置常量，暂未暴露环境变量。）

---

## 10. 快速自检

模拟一次崩溃恢复的最小步骤：

1. 创建一个小批任务（2-3 条子任务），等子任务进入 running；
2. 重启 BKB 后端进程（模拟系统崩溃）；
3. 观察启动日志：`batch dispatcher started`；
4. 约 10-60 秒内观察子任务：`running` 残账被对账器重排为 `pending`
   （`retry_count+1`），随后重新执行并在批次面板显示完成；
5. 数据库侧核对：`SELECT retry_count, error_message FROM ai_chat_sessions
   WHERE batch_id='<批次id>'`——重试痕迹与续跑语义可见，无"永久 running"。
