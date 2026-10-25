# 会话大纲：用户输入精准定位（ZCode 式回合导航） — 设计

日期：2026-10-25 ｜ 状态：已评审（brainstorming 定稿，待出实施计划）
集成位置：AI 对话页 `/ai-chat` 主区右侧 ｜ 不新增菜单/权限
由来：长会话里两次用户输入之间可能隔着几百条工具调用消息，想回到自己
某句输入只能无限滚动或浏览器 Ctrl+F；跨会话搜索命中内容时也只打开会话、
不跳到命中消息。ZCode 的侧边消息大纲是现成参照。

## 1. 背景与目标

AI 对话页现状（相关能力）：

- 消息带全局单调 `seq`（`AiMessage.seq`），后端
  `GET /ai/chat/sessions/:id/messages` 支持 `before`(seq 游标)/`limit`(≤1000)
  分页（`server/routes/ai_chat.py:1095`）；前端初始只加载最新 200 条，
  更早靠「加载更早消息」逐步前插（`loadOlderMessages`，`src/stores/aiChat.ts:459`）。
- 单消息定位机制已存在：`locateTodoStep`（`src/views/ai-chat/AiChatView.vue:600`）
  用 `data-msg-idx` DOM 锚点 + `scrollIntoView` + 2.4s 脉冲高亮
  （`locatedMsgIdx` / `.msg--located`）——但**只对已加载窗口生效**，且锚点是
  数组下标，历史前插后下标会漂移。
- 跨会话搜索 `searchSessions`（`server/routes/ai_chat.py:598`）能按内容命中，
  但 `selectSearchHit`（`AiChatView.vue:347`）只打开会话，不定位到消息。

目标：右侧可折叠「会话大纲」面板列出**全部用户输入**（不受分页窗口限制），
点击任意一条 → 按需加载历史 → 精准滚动定位 + 脉冲高亮；滚动时大纲跟随
高亮当前所在回合；跨会话搜索命中内容时直接跳到命中消息。

已与需求方对齐的决策：

1. 大纲条目 = **仅用户输入**（首行摘要 + 时间），不做回合统计/两级展开；
2. 形态 = **右侧可折叠常驻面板**（约 260px），左侧仍是会话导航；
3. 范围 = 核心（大纲+跨分页跳转）+ 滚动跟随 + 搜索命中定位；
4. 大纲数据源 = **新增轻量后端 `/turns` 端点**（用户回合投影，非全量消息）。

## 2. 非目标

- 公开接管页（external_takeover 蓝图）与 admin 会话管理页（AiSessionAdmin）
  的大纲/定位——入口与渲染链路都不同，另立需求；
- 大纲两级展开（回合内助手摘要/子代理/产物）、回合 token/耗时统计；
- 大纲条目编辑/删除入口（删除及之后消息的既有入口在消息气泡上，不动）；
- 不改既有 `locateTodoStep` 的下标锚点（todo 面板与消息数组同源，下标在其
  使用时机内稳定；大纲用的是新的 id 锚点，两套并存）。

## 3. 架构

```
SessionOutlinePanel.vue（新，右栏）
   │ 点击条目 / 滚动跟随高亮
   ▼
aiChat store：turnsBySession + ensureLoadedUntil(id, seq) + locateMessageById(id)
   │ ensureLoadedUntil：循环 getMessages(before=oldestLoadedSeq, limit=1000)
   │                     前插直到 oldestLoadedSeq ≤ targetSeq 或 hasMore=false
   ▼
GET /api/ai/chat/sessions/:id/turns      GET /api/ai/chat/sessions/:id/messages（既有）
   │ role='user' 投影：id/seq/createdAt/preview
   ▼
ai_chat_messages（既有表，无 schema 变更）
```

- 消息流连续性：`ensureLoadedUntil` 每轮都以当前 `oldestLoadedSeq` 为游标
  前插（与 `loadOlderMessages` 同一手法），不存在两段窗口拼接的空洞风险。
- 权限：`/turns` 复用 `login_required` + `_load_session_for_user`，与
  `/messages` 同口径；无新权限点。

## 4. 后端契约

### 4.1 回合投影端点

`GET /ai/chat/sessions/:id/turns`（`ai_chat.py` 既有蓝图内新增）：

```json
{ "turns": [ { "id": "msg_xxx", "seq": 42, "createdAt": "2026-10-25T09:30:00",
               "preview": "帮我把这批数据按部门汇总" } ] }
```

- SQL：`SELECT id, seq, created_at, content FROM ai_chat_messages
  WHERE session_id=%s AND role='user' ORDER BY seq LIMIT 500`（兜底上限，
  正常会话远达不到；达到时前端大纲末条显示「仅显示前 500 条」提示文案）。
- `preview`（≤160 字符、单行、空白收敛）由服务端提炼：
  - 首个非空 `text` part 截断；
  - 无 text 有 `file` part：`📎 a.xlsx、b.csv`（≤3 个，多余计「等 N 个」）；
  - text 与 file 并存：text 为准（file 忽略）。
- 过滤：`role='user'` 且（有非空 text part **或** 有 file part）。空壳用户
  消息不进大纲。
- 不分页、不返回 content parts、不返回 meta。

### 4.2 搜索命中附带消息锚点

`search_sessions`（`ai_chat.py:598`）：hit 对象新增 `messageId` + `seq`，
**仅 `matchField='content'` 的命中携带**（其命中子查询本就落在 `ai_chat_messages`
行上，顺带返回该行的 id/seq）；`title`/`file`/`preview` 命中的匹配目标是
会话行字段（标题/批输入文件名/last_message_preview），没有对应消息锚点，
置 null。前端类型 `AiSessionSearchHit`（`src/api/aiChat.ts:20` 附近）同步补
可选字段。

## 5. 前端：store（`src/stores/aiChat.ts`）

- 新状态 `turnsBySession: Record<string, SessionTurn[]>`；
  `SessionTurn = { id, seq, createdAt?, preview, local?: boolean }`。
- `loadTurns(id)`：调 `/turns`，写入 `turnsBySession[id]`。挂点：
  - `selectSession`（`aiChat.ts:395` 起）的并行加载队列（loadFiles 等
    旁边，非阻塞）；
  - `_reloadPersisted`（`aiChat.ts:1323`，回合结束收敛点）后刷新——新发送
    的输入进入大纲。
- 大纲最终列表 = 后端 turns + 内存里 id 仍为 `local_*` 前缀的用户消息（在途/
  排队中，尚未落库，不可能出现在 /turns 里）。不重复的原因：发送成功后
  `_transmitUserMessage` 会**就地**把气泡 id 改写成服务端 messageId
  （`aiChat.ts:784-785`），改写后该消息自然只从 turns 来；发送失败则气泡
  被 `_failTurn` 移除。本地补尾条目带 `local` 标记（可显示排队态）。
- 新 action `ensureLoadedUntil(id, targetSeq): Promise<boolean>`：
  - 目标已在窗口内（`oldestLoadedSeq ≤ targetSeq`）→ 立即 true；
  - 否则循环 `getMessages(id, { before: oldestLoadedSeq, limit: 1000 })`
    前插（复用 `loadOlderMessages` 的前插与游标推进逻辑，抽公共私有函数），
    直到覆盖 targetSeq 或 `hasMore=false`；
  - 返回 targetSeq 对应消息 id 是否在 `messages[id]` 中。
- 定位状态：视图侧新增 `locatedMsgId`（id 版脉冲，同 `locatedMsgIdx` 的
  2.4s 自动清除模式）。

## 6. 前端：视图（`src/views/ai-chat/AiChatView.vue` + 新组件）

- 新组件 `src/components/ai-chat/SessionOutlinePanel.vue`：
  - 条目：时间（当日 HH:mm，跨天 MM-DD HH:mm）+ preview 单行省略；
  - props：turns、activeTurnSeq（跟随高亮）、locatedId（点击脉冲联动）；
  - emits：`locate(turn)`。
- 面板挂在 `.ai-chat__main` 右侧（flex 布局加一列），宽 260px；折叠按钮
  在面板自身边缘 + 消息区右上角小图标按钮（面板收起时的再入口）；
  折叠状态 `getStorage/setStorage` 持久化（同 `AI_CHAT_SIDEBAR_WIDTH`
  既有模式，新 key `AI_CHAT_OUTLINE_COLLAPSED`）。
- 消息节点新增 `:data-msg-id="m.id"`（`AiChatView.vue:1540` 的 `.msg`
  div；与既有 `data-msg-idx` 并存）。
- `locateMessageById(id)`：`nextTick` 后
  `document.querySelector('[data-msg-id="..."]')` →
  `scrollIntoView({ behavior:'smooth', block:'center' })` → 置
  `locatedMsgId` + 定时清除。
- 点击条目流程：`ensureLoadedUntil(id, turn.seq)` → 成功：查数组得消息 id
  → `locateMessageById`；失败（目标不在）：刷新 `loadTurns` + ElMessage
  「该消息可能已被删除」。
- 滚动跟随（scroll-spy）：`onChatScroll` 节流（~200ms）计算视口顶部所在
  `.msg`（在 messages-wrap 内按元素位置二分/线性扫）→ 取「seq ≤ 该消息
  的最近用户回合」为 `activeTurnSeq` → 面板对应条目高亮；点击定位后的
  2.4s 内点击高亮优先。
- 面板无 turns（空会话/无用户输入）→ 显示空态文案「暂无输入」。

## 7. 搜索命中定位（`selectSearchHit`，`AiChatView.vue:347`）

打开会话（普通：`selectSession`；批子会话：`ensureBatchInList` →
`selectBatch` → `selectBatchChild`）完成后，若 hit 带 `messageId/seq` →
走 `ensureLoadedUntil` + `locateMessageById` 同一链路。批子会话与普通会话
在 store 同一 `messages` 通道，无分支差异。

## 8. 边界与错误处理

| 场景 | 行为 |
|------|------|
| 目标消息被「删除及之后」清掉 | ensureLoadedUntil 返回 false → 刷新 turns + 提示 |
| 会话流式进行中点击历史回合 | 与流式状态无冲突：流式事件按**消息 id** 定位目标行（`_upsertAssistantPart`，`aiChat.ts:1379` 注释明示 "located BY ID"），part 索引存于目标消息自身 content 内——历史前插两者都不触碰（`loadOlderMessages` 前插同款已在线上验证） |
| `/turns` 请求失败 | 大纲面板显示「加载失败 · 重试」行内按钮，不影响主流程 |
| 会话超过 500 用户回合 | 末条显示「仅显示前 500 条」 |
| 切换会话 | turns 随 selectSession 重新加载；面板保持展开/折叠状态 |
| SSE 断连重连 | 无交互——turns 只在 selectSession/回合结束刷新 |

## 9. 测试策略

（既有要求：新用例须在修复/实现前 commit 上先红；真实链路 E2E。）

- 后端 pytest（`server/tests/` 既有套件）：
  - `/turns`：纯文本会话、纯文件消息（`📎` 摘要）、空文本过滤、空会话、
    权限（他人会话 404 口径同 `/messages`）；
  - `search_sessions`：content 命中带 `messageId/seq`，title/file/preview
    命中为 null（匹配目标是会话行字段，无消息锚点）。
- 前端 vitest：
  - store：`ensureLoadedUntil` 单页命中/多页循环/hasMore 耗尽返回 false、
    turns 本地 `local_*` 补尾与服务端去重；
  - `SessionOutlinePanel`：条目渲染、时间跨天格式、点击 emit、active 高亮。
- Playwright E2E（`e2e/ai-full/`，复用 `seedPlainSession`（`db-helpers.ts:33`）
  造 260+ 消息长会话）：
  1. 打开长会话 → 大纲显示全部用户输入（多于已加载窗口内的条数）；
  2. 点最旧回合 → 「加载更早消息」自动完成（多页）→ 滚动定位 + 脉冲可见；
  3. 向下滚动 → 大纲跟随高亮切换；
  4. 侧栏搜索内容命中 → 打开会话并定位到命中消息。

## 10. 文件清单

| 文件 | 变更 |
|------|------|
| `server/routes/ai_chat.py` | +`/turns` 端点；`search_sessions` hit 附 `messageId/seq` |
| `src/api/aiChat.ts` | +`getTurns()`；`AiSessionSearchHit` 补字段 |
| `src/stores/aiChat.ts` | +`turnsBySession`/`loadTurns`/`ensureLoadedUntil`；挂点 selectSession、_reloadPersisted |
| `src/components/ai-chat/SessionOutlinePanel.vue` | 新组件 |
| `src/views/ai-chat/AiChatView.vue` | 右栏布局、`data-msg-id`、`locateMessageById`、scroll-spy、selectSearchHit 跳转 |
| `server/tests/`、`src/**/__tests__/`、`e2e/ai-full/` | 上述测试 |
