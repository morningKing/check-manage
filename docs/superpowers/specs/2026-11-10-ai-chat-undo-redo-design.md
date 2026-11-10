# AI 会话撤销/重做设计（对齐 OpenCode undo 语义）

- 日期：2026-11-10
- 来源：用户需求「OpenCode 有 undo 功能，可以切换到之前的会话（状态）上，平台 AI 会话支持吗」→ 调研结论：不支持（现有「编辑/重新生成」只删平台消息镜像，OpenCode 会话未回退）。本设计补齐为真撤销。
- 状态：已批准（2026-11-10，四个决策点按推荐落定，见 §0.2）
- 证据：OC 1.19.3 二进制源码级解读 + stub-LLM 隔离探针三场景活体实证（输出在仓内
  `.dev-ai-workspaces/undo-probe-scripts/`：`run-output.txt` 全量判定 + `stub-requests.jsonl`
  模型视角请求留痕；探针栈已拆净，脚本可复用）+ OC 2.x（dev HEAD `ed0171f`）源码级语义调研
  + 平台耦合面全图（`ai_chat_messages` 22 个读取点 / 15 个写入点）。

## 0. 范围与决策记录

### 0.1 范围

**只开放普通交互会话**（本人所有、`kind='chat'`、无 `batch_id` / `orchestration_run_id` /
非客服会话）。批任务与编排子会话（含终态）保持 409 不可撤销——批产出不可变的审计口径。
撤销语义＝**回退到某条用户消息之前**（该消息及其后全部回合）。

不做（非目标）：

- 编辑/重试时恢复附件 chips（现状保持：只装回文本）；
- 部分重做 UI（撤销点前移/后移只走「全量重做」；OC 原生支持更细粒度，v1 不暴露）;
- 分支会话（fork）、批子会话撤销、`outputs/` 交付物回滚；
- 撤销轮的记账（agent_tool_calls）/记忆提取/perf 分析侧表回滚（审计口径：真实发生过，保留）。

### 0.2 已拍板的决策（2026-11-10 用户批准）

1. 「编辑/重新生成」**直接升级为真撤销**（文件跟着还原），不加开关灰度；
2. 轮次 >7 天 / 锚点失效 / OC revert 失败时**自动降级为重建式**（上下文近似回退、文件不
   还原、可重做），不禁用按钮；
3. `/undo` `/redo` 前端本地命令**要做**；
4. 跨标签页即时同步（进程内推送）**一期做**。

### 0.3 关键实证（设计依据，防重查）

| # | 事实 | 来源 |
| --- | --- | --- |
| E1 | `POST /session/:id/revert {messageID}` 软撤销：消息不物理删（REST 仍全量），会话带 `revert={messageID,partID,snapshot,diff}` + `summary{additions,deletions,files}` | 探针 A1 |
| E2 | **必须传 user 消息 id**：传 assistant id 会吸附到所属 user 消息（撤销点正确）但只收集目标之后的 patch → **文件不还原**（对位陷阱） | 探针 A3 |
| E3 | 下次 prompt / summarize 开头自动 cleanup：物理删消息并逐条发 `message.removed` 事件；提交后 unrevert 无效 | 探针 A4 + 二进制 |
| E4 | unrevert＝还原文件＋清标记；已撤销态可继续往前撤、也可把撤销点后移（共用首次撤销前快照）；单次 unrevert 回到首次撤销前状态 | 探针 A2/A5 |
| E5 | busy（回合运行中）→ 409 `SessionBusyError`；abort 后即可 revert | 探针 A6 |
| E6 | 格式合法但不存在的 messageID → **200 且不产生撤销态**（平台必须校验响应 `revert.messageID`） | 探针 A7 |
| E7 | 上下文真回退：revert 后再发 prompt，模型请求体不含被撤轮次、含撤销点之前的轮次 | 探针 A4（stub 请求留痕） |
| E8 | 文件还原走 OC 快照（独立 git-dir `snapshot/<project.id>/<hash(worktree)>`，不碰工作区 `.git`，会话间隔离）；write / bash / 子代理改动均按步还原 | 探针 A1 + 二进制 |
| E9 | 被工作区 `.gitignore` 忽略的 `outputs/`、`uploads/` **不受快照保护**；非 git 工作区快照整体失效但上下文回退仍有效；嵌在被忽略外层仓库内（无自有 .git）＝`vcs=git` 但 `files=0` 静默不还原 | 探针 A1/B/C |
| E10 | 快照对象 `gc --prune=7.days`：**>7 天轮次的文件还原不可靠**（本机 snapshot 库存在 0 对象老目录佐证） | 二进制 + 本机抽查 |
| E11 | 事件序列：revert→`session.diff`+`session.updated(revert)`；commit→`message.removed`×N；unrevert→`session.updated(revert=None)` | 探针事件流 |
| E12 | OC 2.x 无 v1 兼容路由：revert→`POST /api/session/:id/revert/stage {messageID, files?}`、unrevert→`DELETE /api/session/:id/revert`、commit→prompt/compact 内自动或显式 `/revert/commit`；事件改为 `session.revert.staged/cleared/committed`（**无** `message.removed`）；未知 id 404；快照开关 `snapshot`→`snapshots` | OC2 dev HEAD 源码 |

## 1. 语义与能力分级

**撤销（undo to message X）**＝ X 及之后所有消息从界面消失、模型上下文回退、该范围内 AI
对工作区受快照保护文件的改动按步还原、可重做；**下一次产生新消息的操作即提交**，提交后
不可重做（OC 原生生命周期，平台原样继承）。

| 场景 | 上下文 | 文件 | 重做 | 模式 |
| --- | --- | --- | --- | --- |
| 正常（git 工作区、目标轮 ≤7 天、锚点可解析） | ✓ 原生 | ✓ | ✓（至下次发送） | native |
| 非 git 工作区 / 嵌套被忽略 | ✓ 原生 | ✗（OC 不跟踪，UI 明示） | ✓ | native |
| 目标轮 >7 天 / 锚点失效 / OC revert 失败 | ✓ 近似（历史重放） | ✗ | ✓ | rebuild |
| 批/编排/客服会话 | 不开放（409，沿用现有保护） | — | — | — |

能力分级对用户透明标注：确认弹窗与重做 banner 按实际模式给出文件数与「文件不会还原」提示。

## 2. 落点

### 2.1 迁移（三处落点照惯例）

新表 ×2 + `ai_chat_sessions` 一列，迁移文件 `migrations/2026_11_10_ai_chat_undo_redo.py`
+ `app.py` 注册块 + `db_schema/` 基线 DDL：

```sql
CREATE TABLE ai_chat_message_stash (
    message_id  VARCHAR(100) PRIMARY KEY,
    session_id  VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
    role        VARCHAR(20) NOT NULL,
    content     JSONB NOT NULL,
    meta        JSONB,
    created_at  TIMESTAMPTZ NOT NULL,
    seq         BIGINT NOT NULL,
    rating      SMALLINT,
    stashed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_msg_stash_session ON ai_chat_message_stash(session_id, seq);

CREATE TABLE ai_chat_session_reverts (
    session_id  VARCHAR(100) PRIMARY KEY REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
    mode        VARCHAR(10) NOT NULL,          -- 'native' | 'rebuild'
    opencode_session_id VARCHAR(200) NOT NULL,  -- 撤销所针对的 OC 会话（重做前校验未被换过）
    oc_message_id VARCHAR(200),                -- native：OC 撤销点 user 消息 id（OC 侧无对应轮时为空）
    from_seq    BIGINT NOT NULL,
    message_count INT NOT NULL,
    file_count  INT NOT NULL DEFAULT 0,
    file_names  JSONB,                          -- 还原(或本应还原)文件名清单
    prev_opencode_session_id VARCHAR(200),      -- rebuild：重做要切回的旧 OC 会话
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE ai_chat_sessions
    ADD COLUMN context_replay_pending BOOLEAN NOT NULL DEFAULT FALSE;
```

选 stash 搬行而非行内标记：`ai_chat_messages` 有 22 个读取点（全局搜索/大纲/perf/导出/
open API/接管页…），搬出/搬回保真（`seq`、`created_at`、点赞随行携带；`search_text` 触发器
随 INSERT 重建），全部读取方零改动。`ai_chat_message_feedback` 对消息行是
`ON DELETE CASCADE`——搬行前先 JOIN 取 rating 存入 stash，重做时回插。

### 2.2 核心模块 `server/utils/ai_revert.py`

纯逻辑与 SQL 收敛在一处，路由只做守卫与编排。

**(a) 锚点解析 `resolve_undo_target(sid, msg_id, oc_messages)`（纯函数，独立单测）**

平台 assistant 行 id＝该回合第一条 OC assistant 消息 id（`chat_persist.py:138` 落
`turn_msg_id`），是天然锚点；用户行 id 平台自生成、无 OC 对应。规则：

1. 目标用户行（`seq` 升序）之前**最近一条** id 存在于 OC 消息列表的 assistant 行为锚
   （跳过 `run_script` 等平台自造行：其 id 不在 OC 列表，天然被跳过）；
2. 无锚（目标即会话首条，或更早行全是平台自造）→ OC 目标＝OC 列表第一条 user 消息；
3. 有锚 → OC 目标＝锚之后**第一条 OC user 消息**（自然跨过压缩轮 / 从未到达 OC 的失败轮）；
4. 锚存在但不在 OC 列表（会话恢复/重建过，`_recover_session_and_resend` 换过 OC 会话）→
   `mode='rebuild'`；
5. 产出 plan：`{mode, oc_user_message_id, anchor_row_id, rows(seq>=目标.seq), files[], file_capability}`。
   `files`＝OC 目标之后所有 `patch` part 的文件并集（E8 范围，据此算 N）；
   `file_capability`：`none`（E9 三形态之一，判据 `GET /project/current` 的
   `vcs!='git'` 或 `worktree` 归一化后 ≠ 工作区路径，或无 patch 文件）/ `stale`
   （OC 目标 user 消息 `time.created` 距今 > `AI_UNDO_FILE_SNAPSHOT_MAX_AGE_DAYS`，
   默认 7，E10）/ `full`。`stale` 或解析失败 → `mode='rebuild'`。

**(b) `undo_from_message(sid, msg_id, user)` 执行序（native）**

1. 守卫：所有权 404 / `_execution_controlled` 409（沿用）/ busy 409（`has_listener(sid)` 为真
   即拒，OC 409 兜底映射）；
2. `pg_advisory_xact_lock(hashtext(sid))` 开事务；
3. 取平台行 + OC `get_messages` → `resolve_undo_target` → plan；
4. rebuild 分支见 (d)；
5. native：`stop_listener(sid)`（防监听器复活竞态，`clear_session` 先例；busy 守卫下通常
   已无活监听器，纯防御）→ 事务内先搬行（`INSERT … SELECT` 消息+rating → `DELETE`；
   `seq >=` 判据）→ 调 `revert_stage(oc_sid, plan.oc_user_message_id)`（20s 超时）→
   **校验响应 `revert.messageID == plan.oc_user_message_id`**（E2/E6）→ upsert 状态行 →
   提交事务。OC 消息列表为空（被撤轮从未到达 OC，如 dispatch 失败轮）→ 不调 OC，仅平台侧
   搬行（native、`oc_message_id` 空、`file_count=0`）；
6. OC 失败/校验不过 → 整体回滚（**报错＝什么都没改**）；DB 提交失败而 OC 已撤 → 反向
   `revert_clear` 尽力补偿 + `logger.error`；
7. 广播 `session.revert`（§2.6）+ oplog + 返回 `{messages: <新窗口>, revert: <状态>}`。

**(c) `redo(sid)`**：状态行存在 → 校验 `sessions.opencode_session_id ==
状态行.opencode_session_id`（中途被恢复/清空换过 OC 会话则拒绝重做、直接 `commit_revert`
收口）→ 事务内调 `revert_clear`（native 且 `oc_message_id` 非空时；校验撤销态已清）→
stash 搬回（含 feedback 回插、原 `seq`/`created_at` 保真）→ 删状态行 → 提交。无状态 →
409 `NOTHING_TO_REDO`。

**(d) rebuild 分支**：`client.create_session(directory=ws, title='撤销重建')` → UPDATE
`opencode_session_id` + `context_replay_pending=true` → 搬行 + 状态行（`mode='rebuild'`,
`prev_opencode_session_id=旧值`）。`send_message` 在 dispatch 前若 `context_replay_pending`：
`prompt = render_history_block(sid, exclude_msg_id=msg_id) + prompt` 并清标记（复用
`session_history.render_history_block` 既有机制）。重做：切回 `prev_opencode_session_id`、
清标记、尽力 `delete_session(新空会话)`、搬回行。

**(e) `commit_revert(sid, conn)`（提交）**：`DELETE stash WHERE session_id`；`DELETE 状态行`；
清 `context_replay_pending`；best-effort 清理被撤内容引用的孤儿 `ai_chat_subtasks` 行
（扫 stash content 的 `subtask_use.subtaskId`）。

### 2.3 端点（`server/routes/ai_chat.py`）

| 端点 | 语义 |
| --- | --- |
| `GET /sessions/<sid>/undo-preview?messageId=` | 调 (a) 出 plan：`{messageCount, files[], fileCapability, mode, reason?}`，只读 OC（1 次 `get_messages` [+1 次 `project/current` 可缓存]） |
| `POST /sessions/<sid>/undo {messageId}` | §2.2(b)；成功返回 `{messages, revert}`；错误码沿用现有风格：`SESSION_BUSY` 409 / `OPENCODE_UNAVAILABLE` 502 retryable / `MESSAGE_NOT_FOUND` 404 / `BATCH_SESSION_CONTROLLED` 409 |
| `POST /sessions/<sid>/redo` | §2.2(c)；`NOTHING_TO_REDO` 409 |
| `GET /sessions/<sid>/messages` | 响应加 `revert` 块（无撤销态时 `null`）——任何端打开页面即收敛到撤销态并显示 banner |
| `DELETE /sessions/<sid>/messages/<msg_id>`（现有） | 语义升级为同一 undo 核心：三个现有调用方（编辑/重新生成/发送失败清理）平滑迁移；发送失败清理路径天然 no-op OC（该轮从未到达 OC） |

### 2.4 提交时机挂点

| 挂点 | 时机 | 说明 |
| --- | --- | --- |
| `send_message` | dispatch 成功后（`dispatch.ok` / recovery 后） | OC 侧 prompt 开头自动 commit（E3），双向对齐；dispatch 失败保留重做窗口 |
| `run_session_command` | 同上 | `/command` 同样触发 OC cleanup |
| `compact_session` | summarize 接受后 | OC compact 亦自动 commit |
| `run_script` | 写入其 assistant 行之前 | 平台侧先行；OC 到下次 prompt 自然收敛 |
| `clear_session` / `delete_session` | 连带 `commit_revert` + 清 stash/状态 | 软删/清空语义下不留悬挂 |

### 2.5 运行时适配（OC 2.0 前向兼容）

`server/utils/runtime/base.py` 能力三元组 + `capabilities()` 加 `'revert'`；
`OpenCodeClient` 实现 v1.19.3；`StubClient` 内存模拟（单测/压测栈可用）。路由经
`get_runtime().get_client()` 调用（与 `send_message` 同路，保证 stub 可测）。

| 平台语义 | 1.19.3 | OC 2.x（迁移时只改 adapter） |
| --- | --- | --- |
| `revert_plan` | `GET /session/:id/message` 扫 patch parts | 同路径读 `snapshot.files` |
| `revert_stage(oc_sid, message_id)` | `POST /session/:id/revert {messageID}` + 校验响应 `revert.messageID` | `POST /api/session/:id/revert/stage {messageID}`（未知 id 404，语义更明确） |
| `revert_clear(oc_sid)` | `POST /session/:id/unrevert` | `DELETE /api/session/:id/revert` |
| busy | 409 `SessionBusyError` | 同 tag（官方客户端 interrupt→wait→stage，迁移时对齐） |

### 2.6 多端同步（进程内推送）

SSE 代理（`sse_events`）已有每连接队列（`_pump_upstream_events`）。新增模块级
`sid → {连接队列}` 注册表（连接建立注册、`finally` 注销），undo/redo/commit 提交后调用
`_broadcast_session_event(sid, 'session.revert', {sessionId, revert, reason})` 向该会话全部
连接推送。不依赖 OC 事件形态（1.x 的 `message.removed` 无法覆盖平台 user 行——其 id 在 OC
侧无对应；2.x 连 `message.removed` 都没有，E11/E12）。前端：

- `createEventStream` allow-list 加 `session.revert`（`src/api/aiChat.ts` 12 事件白名单，
  守护用例 `src/api/__tests__/aiChat.test.ts:62` 同步更新）；
- store 收到后防抖 300ms 重拉 `getMessages` + revert 状态，**走专用强一致路径绕过
  `_reloadPersisted` 的「拒收变短列表」守卫**（`aiChat.ts:1469`）；
- 发起端本身也收同一事件（幂等，重拉无害；消除与 DB 提交的时序竞态）。

### 2.7 前端

- **store**（`src/stores/aiChat.ts`）：`deleteFromMessage` 改为调 undo 端点、**失败即不裁剪**
  （现状吞错照样裁，顺手修掉）；`retryUserMessage` 不变（内部走 undo）；新增
  `undoFromMessage/redoSession`，成功后以服务端窗口为准 + 更新 `revertState[sid]`；
- **确认弹窗**（三处按钮统一）：`将撤销该消息及之后 N 条对话，并把工作区 M 个文件还原到
  该消息之前。发送新消息后将无法重做。`；`fileCapability='none'` 或 `mode='rebuild'` 时改为
  「文件不会还原」版本；数据来自 undo-preview；
- **重做 banner**：composer 卡片内、输入框上方一行（`.auto-approved` 同区位，
  `data-test="revert-banner"`）：`已撤销 N 条消息（还原 M 个文件）· 重做 · ✕`，发送即消失；
- **`/undo` `/redo` 命令**：`FRONTEND_COMMANDS` 本地命令（`src/components/ai-chat/chat-commands.ts`），
  `/undo`＝撤销最后一个用户轮 + 文本装回输入框（对齐 OC TUI），免确认（banner 兜底披露）；
- 撤销/重做后刷新 `loadChanges`/`loadFiles`（文件已物理变化）；
- `editMessage` 现有 `local_` 前缀守卫、`streaming` 禁用逻辑不变。

### 2.8 审计与日志

- `log_operation('update', 'ai_chat_session', sid, sid, f'撤销到消息 {msg_id}（N 条，M 文件）')`
  / `'重做（恢复 N 条）'`（补齐 oplog 覆盖口径）；
- `ai-chat.log` 结构化行：undo/redo/commit/rebuild 各一条（`session= oc= mode= rows= files=`
  要素齐全），排障可循。

## 3. 测试

- **pytest**（`server/tests/`，独立库）：
  - 锚点解析矩阵（§2.2(a) 全分支：首条消息/失败轮在目标前/压缩轮穿插/run_script 行穿插/
    锚失效→rebuild/无锚→OC 首条 user）；
  - stash 搬出/搬回保真（`seq`/`created_at`/rating/search_text 触发器）；
  - 提交矩阵（§2.4 五挂点 + dispatch 失败保留窗口）；
  - 原子性（OC stage 失败→DB 零变化；DB 提交失败→反向补偿路径）；
  - busy 409、`NOTHING_TO_REDO`、`BATCH_SESSION_CONTROLLED` 维持；
  - clear/delete 连带清理；StubClient revert 模拟闭环；
  - **改造现有 3 个用例**（`test_routes_ai_chat.py:987/1005/1016`）到新语义
    （`test_review_gap_tests.py:2310` 的 409 断言不变）。
- **vitest**：store 失败不裁剪、banner 状态机、`/undo` 命令、allow-list 守护更新。
- **E2E（0-LLM 真实链路，新 `e2e/manual/undo-probe/`）**：以本次探针为底（stub LLM +
  隔离 OC serve + 隔离后端/DB，gate-leak-probe 同款编排骨架；端口 stub :3198 / serve :4199 /
  backend :3094 / DB `casemanage_undoprobe`，与既有栈错开）。断言三件套：
  1. **上下文真回退**：撤销后发送，stub `stub-requests.jsonl` 中新请求不含被撤轮次、
     含撤销点之前轮次（E7 复现）；
  2. **文件物理还原**：write/bash 改动按步回滚；`outputs/` 不回滚；
  3. **平台一致性**：消息窗口/revert 状态/跨标签页收敛/重做恢复/提交后重做消失/
     >7 天与锚失效走 rebuild。
  另配一条真模型 `@llm` 冒烟（ai-full）。所有新用例先于修复 commit 上跑红。
- **性能**：undo/preview 各 1 次 OC 调用 + 索引化 stash 搬运；热路径仅增每发送一次
  `SELECT 1 FROM ai_chat_session_reverts`（PK 探测）。

## 4. 风险与开放点

1. **git clone 出的子仓库内部改动不受快照保护**（嵌套仓库只进 gitlink，推断自 git 机制；
   实施时探针补证，UI 侧「文件不会还原」清单里如实展示即可）；
2. 子代理（task 子会话）与父共享工作区，其文件改动经父回合 patch 还原（推断，E8 机制覆盖）；
   子代理自身的 OC 子会话不被 revert——其历史残留在 OC 侧，无平台可见影响；
3. 撤销轮的账本/记忆/perf 侧表不回滚（已接受，§0.1）；
4. 单进程假设：advisory lock 跨进程成立，`has_listener` 与 SSE 注册表仅进程内——多进程部署
   需换 Redis 类总线（与现有 `has_listener` 同界，不扩大）；
5. OC 2.x 迁移时：事件模型差异已在 §2.5/§2.6 隔离到 adapter 与平台自有事件；busy 的
   interrupt→wait→stage 序列待迁移时对齐；
6. OC 侧 snapshot/project 目录随历史会话累积、只增不减（既有运维事实，不属本设计扩大）；
7. `undo-preview` 的 `files` 清单来自 patch parts，与实际还原集在「目标轮内既有还原又有
   新改」的极端交叠下可能有±（OC 按「每文件首次触及步」还原）；弹窗文案用「约 M 个文件」。
