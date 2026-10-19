# 子代理执行轨迹可见性 设计

**日期**：2026-08-12
**状态**：设计已确认，待编写实施计划

## 1. 问题

OpenCode 委托子代理时会产出一种专门的消息 part（`type: 'subtask'`，schema 名 `SubtaskPart`，携带 `sessionID`/`agent`/`prompt`/`description`）——子代理是在自己独立的 OpenCode session 里跑的，理论上它的完整对话轨迹可以通过这个 `sessionID` 单独查到。

但全仓零处理这个 part 类型。三处负责把 OpenCode 消息 parts 映射成存库/渲染内容的代码——`server/utils/chat_persist.py::apply_event`（实时聊天 SSE 持久化）、`server/utils/batch_engine.py::_content_from_parts` 与 `_OpenCodeFacade.list_messages`（批任务）——只认 `text` 和 `tool` 两种类型，`subtask` 落进 if/elif 链没有分支接住，直接被丢弃。用户看不到子代理委托发生过，更看不到它内部做了什么，只能等最终结果。

`server/utils/batch_engine.py` 里有条注释描述"委托子代理表现为一个长时间运行的 `task` 工具"——这是过时认知，按当前装的这版 OpenCode 的 schema，委托走的是专门的 `subtask` part，不是普通 `tool` part。

## 2. 范围决策

| 维度 | 决策 |
|---|---|
| 覆盖面 | 实时 AI 聊天 + 批任务对话查看器（管理员页与用户侧均覆盖） |
| 可见深度 | **完整嵌套轨迹**——展开气泡能看到子代理自己调用的工具、产出的文本，不止黑盒摘要 |
| 持久化 | **永久保存**，关闭会话再打开仍能看到，与仓库现有"一切都落库、不依赖 OpenCode 的实时/临时状态"的约定一致 |
| 嵌套深度上限 | 5 层，超过只显示「已达展示深度上限」，不再渲染/持久化更深层级 |

## 3. 数据模型与持久化架构

### 3.1 新建两张专用表，不改动 `ai_chat_sessions`/`ai_chat_messages`

`ai_chat_sessions` 有三个 `NOT NULL` 列专为"用户能连接的顶层会话"设计：`workspace_path`（独立工作目录）、`session_token`（MCP 鉴权令牌）、`token_expires_at`。子代理不需要这些——它复用父会话同一个工作目录，也不该被独立连接（不应该存在一个能把自己伪装成某个子代理去连 MCP 的令牌）。硬塞进去意味着要么编造一个没人用但能通过鉴权的 token（埋不必要的攻击面），要么把这三列改可空 + 加判别列，然后审计全仓每一处读 `ai_chat_sessions` 的地方（侧边栏列表、管理治理列表、批任务管理页列表、备份的字段清单），确保不会把子代理行错误地列出来。

新建：

```sql
CREATE TABLE ai_chat_subtasks (
  id                  VARCHAR(100) PRIMARY KEY,
  root_session_id     VARCHAR(100) NOT NULL REFERENCES ai_chat_sessions(id) ON DELETE CASCADE,
  parent_subtask_id   VARCHAR(100) REFERENCES ai_chat_subtasks(id) ON DELETE CASCADE,
  parent_part_id      VARCHAR(100),
  opencode_session_id VARCHAR(200),
  agent               TEXT,
  prompt              TEXT,
  description         TEXT,
  status              VARCHAR(20) NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running','completed','failed')),
  created_at          TIMESTAMPTZ DEFAULT NOW(),
  completed_at        TIMESTAMPTZ
);

CREATE TABLE ai_chat_subtask_messages (
  id          VARCHAR(100) PRIMARY KEY,
  subtask_id  VARCHAR(100) NOT NULL REFERENCES ai_chat_subtasks(id) ON DELETE CASCADE,
  role        VARCHAR(20) NOT NULL,
  content     JSONB NOT NULL,
  meta        JSONB,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  seq         BIGSERIAL
);
```

- `root_session_id`：归属哪个顶层会话，RBAC 作用域判定用它（不管嵌套多深，都能一步查到"这条数据归谁"）。
- `parent_subtask_id`：自引用，非空表示这是更深一层的子代理（子代理委托的子代理）。自然支持任意深度，不需要额外的深度计数列。
- `parent_part_id`：OpenCode 自己的 part id（`SubtaskPart.id`，`prt...` 格式的不透明字符串），**不是**指向本仓库任何表的外键。渲染定位靠占位 part 自带的 `subtaskId`（见 §5），不依赖这一列；它的作用是**幂等键**——同一个子代理委托实例的 part id 在 OpenCode 侧多次更新时保持不变，持久化时用它判断"这个子代理行是不是已经建过了"，避免同一次委托被重复建行。
- `seq BIGSERIAL`：直接用序列排序，不重蹈 `created_at` 排序在同事务批量写入时不确定的老问题（这个仓库已经因为这个坑加过 `ai_chat_messages.seq` 列，这次从一开始就用对的）。

代价是这两张表在结构上跟 `ai_chat_sessions`/`ai_chat_messages`有点像。为了不让持久化逻辑也跟着重复三份，把现在散在 `chat_persist.py`（实时）和 `batch_engine.py`（批任务）里那两套"OpenCode parts → 存库内容"的映射逻辑抽成一个共享的纯函数模块 `server/utils/opencode_parts.py`，两条路径都调它；新加的 `subtask` part 类型也在这一处统一处理——顺带消掉现有的重复。

**这一步抽取对现有的 `text`/`tool` part 映射必须是行为不变的纯搬迁**——只挪代码位置、只新增 `subtask` 分支，不得在搬迁过程中顺手改动既有两种 part 类型的映射逻辑（哪怕看着像"顺便优化"）。这两处映射目前分别服务实时聊天与批任务，各自都有对应的既有测试，抽取后这些既有测试必须原样通过，作为"没有引入行为变化"的证据。

### 3.2 深度限制

递归处理 `subtask` part 时带一个深度计数，超过 5 层就不再继续下探（不建子代理数据行、不渲染可展开气泡），只在到达上限的那一层留一句「已达展示深度上限」。

## 4. 两条集成路径

### 4.1 实时聊天（SSE，`chat_persist.py`）

改动集中在 `apply_event`。现在它一看到 `ev_sid != opencode_session_id` 就丢弃；改成维护一个"已知子代理 sessionID"集合，初始只有顶层会话自己。每当某条消息里出现 `type=='subtask'` 的 part（不管是顶层会话的还是已知某个子代理自己产出的，递归地），就把它携带的 `sessionID` 收进这个集合，并在 `ai_chat_subtasks` 里建一行（`status='running'`）。之后事件只要 `ev_sid` 落在集合里（不限于顶层），就路由进对应的累加器持久化，而不再是"不等于顶层就丢"。

定时增量持久化（`INCREMENTAL_PERSIST_INTERVAL`）同样对每个已知子代理的累加器各自触发一次——子代理跑到一半时用户展开气泡就能看到当前进度，不用等它跑完。

**竞态不需要额外设计**：`message.part.updated` 每次带的是该 part 的完整当前状态，不是增量补丁（现有 `apply_event` 直接整体覆盖 `parts_by_id[pid]`，不是合并）。哪怕子代理会话的第一条事件因为时序原因被漏掉（那时它的 `sessionID` 还没被父级的 `subtask` part 记录进"已知集合"），下一次更新会带着完整状态重新覆盖，不会丢数据，只是最开始一瞬间可能有小的展示延迟。

**SSE 重连不需要特殊处理**：子代理"已知集合"是服务端持久化监听线程（`chat_persist.py` 的 per-session daemon thread）的内存状态，跟浏览器的 SSE 代理连接是解耦的——浏览器重连不影响服务端这个线程已经积累的状态，这是现有架构本来就有的解耦，不需要为子代理专门做什么。

### 4.2 批任务（REST 轮询，`batch_engine.py`）

`_await_finished` 轮询顶层子任务消息时，同样检测新出现的 `subtask` part，发现新的子代理 sessionID 就把它加进"这一轮也要轮询"的列表，用同一个 `get_messages(childSessionID, directory=同一个工作目录)` 定期拉取并持久化——不需要新的 OpenCode 客户端能力，子代理复用父会话的工作目录，直接传同一个 `directory` 参数即可。

**完成判定不需要额外处理**：委托子代理本身是父级的一次阻塞式工具调用，OpenCode 不会把父级那一轮标记为完成直到子代理跑完，现有的完成检测逻辑（含今天刚修的 `_TurnFailed` 错误透出）天然覆盖这一点。

**子代理自己的轮次失败时必须能看到原因**：今天早些时候刚修过的那个 bug（OpenCode 在 `AssistantMessage.error` 上报具体失败原因，我们的映射逻辑之前不读它，导致"无进展"误报，误导用户干等 180 秒）必须在子代理这条路径上从一开始就接住——`opencode_parts.py` 的映射逻辑复用同一套已经修过的错误透出逻辑。子代理失败时 `ai_chat_subtasks.status` 要落 `failed`，这样折叠态的气泡上就能直接看到失败图标，不需要展开了才发现"其实它挂了"。

## 5. 前端渲染

新建一个共享的递归组件 `SubtaskBubble.vue`（视觉上是 `ToolCallBubble.vue` 的近亲——头部一行显示委托的 agent 名称、description、状态图标，点击展开）。在三处复用同一份：`AiChatView.vue` 的消息循环里、批任务的 `BatchConversationView.vue` 的消息循环里、以及它自己内部（子代理又委托了子代理时递归嵌一层）。

**惰性加载，不随对话一起下发**。父级消息的 `content` 里，子代理委托只作为一个轻量占位 part（`{type:'subtask_use', subtaskId, agent, description, status}`），不含嵌套消息本体。用户点开气泡才发请求拉取：

- 实时聊天（用户拥有归属会话）：`GET /ai/chat/sessions/:sid/subtasks/:subtaskId/messages`
- 批任务管理页（管理员跨用户）：`GET /ai/chat/admin/batches/:batchId/sessions/:sid/subtasks/:subtaskId/messages`

两个端点共用同一个仓储查询函数，分别包一层不同鉴权（所有权校验 / `admin.ai_chat_admin`），用一个可选的作用域参数区分（跟 `api_key_id: str | None = None` 那个既有模式一致，不写成两个互相独立、容易走漏其中一个校验分支的函数）。子代理的子代理（嵌套更深）也走同一个端点，只是把 `subtaskId` 换成孙代的 id，鉴权靠该行上的 `root_session_id` 一路追溯，不用知道直接父级是谁。猜错 `subtaskId` 一律 404，不泄漏存在性。

**渲染内容刻意从简**：只有文本 + 工具调用气泡（复用现成的、已经很通用的 `ToolCallBubble.vue`）+ 递归的子代理气泡，不接画板卡片、TODO 列表、查询结果块这些 `AiChatView.vue` 特有的富交互渲染。这跟批任务管理页那次的取舍是同一个道理——子代理的转录是给人看"它干了什么"用的，不是可交互工作区，在别人的只读转录里去"运行"一段代码没有意义。代价是这个新组件跟 `AiChatView.vue` 内联的富渲染逻辑有点重复（文本、工具调用两种 part 的映射逻辑），但换来的是不用碰 `AiChatView.vue` 那 1300+ 行——延续上一个分支同样的原则：不重构核心交互路径，接受渲染逻辑的部分重复。

**500 条截断**同样套用到这两个新端点（哪怕现有的顶层 `get_messages` 端点没有这个上限）——子代理跑起来做大量文件探索时产出的工具调用消息可能很多，新端点从一开始就有界，不给自己埋雷。

**深度限制在组件层体现**：`SubtaskBubble.vue` 带一个 `depth` prop，超过 5 就不再渲染可展开的子气泡，只显示提示文案。

## 6. 边界情况

1. **子代理轮次失败必须透出原因**（见 §4.2）。
2. **归属校验**：两个新端点都要 404（不是 403）应对"存在但不属于你"的情况，与仓库既有惯例一致。
3. **排序用 `seq`，不是 `created_at`**——这个坑在本仓库已经出现过三次以上，计划里的每一处测试都要显式验证。
4. **500 条截断**（见 §5）。
5. **清空会话（`clear`）要连带清子代理数据**。会话删除靠外键 `ON DELETE CASCADE` 自动清干净（含批任务子会话删除，因为子代理归属的 `root_session_id` 本身就是 `ai_chat_sessions` 的行）；但"清空"操作是原地重置、不删会话行本身，现有逻辑只删 `ai_chat_messages`——需要在这个操作里补一步，把该 `root_session_id` 下的子代理数据也删掉，否则留下一批没有任何消息引用得到的孤儿数据。
6. **备份/还原清单**。本仓库在这条分支之前已经因为"忘了把新表加进备份列清单"栽过三次（`row_actions`、`seq`、`meta`）。计划里要显式列一步：把 `ai_chat_subtasks`/`ai_chat_subtask_messages` 加进 `server/utils/backup.py` 的备份与还原表清单，并专门标出"这是历史上反复被忘记的一步"提醒实施者。

## 7. 测试与验证

**后端**
- 排序鉴别力：构造 `created_at` 全同、`seq` 乱序的夹具，变异验证改回 `created_at` 排序会让测试变红。
- 归属越权：猜一个不属于自己的 `subtaskId` → 404；管理员端点同理但换成能力键判定，变异验证"换成弱鉴权后测试是否还能抓到"。
- 错误透出：构造子代理消息带 `error` 字段 → 断言 `ai_chat_subtasks.status` 落 `failed` 且失败原因可读，不是无限期停在 `running`。
- 深度限制：构造 6 层嵌套 → 第 6 层不落库/不渲染，只留提示。
- 截断：>500 条子代理消息 → `truncated` + 总数，同批任务那套形状一致。
- 清空会话：`clear` 之后回查子代理表确认无孤儿数据残留。
- 备份/还原：两张新表的字段 round-trip 测试。

**前端**
- 递归组件懒加载：未展开时不发请求；展开后发请求且只发一次。
- 深度上限在组件层的渲染截断。

**Playwright + proxy.py**（CLAUDE.md 强制项）：两条路径各走一遍——实时聊天里触发一次会委托子代理的对话，展开气泡看到嵌套转录；批任务管理页的对话查看器里同样展开验证。新增的两个端点经 `:8080` 实测非 404。

## 8. 文件清单

**新建**

| 文件 | 职责 |
|---|---|
| `server/utils/opencode_parts.py` | 共享的"OpenCode parts → 存库内容"映射函数（含 `subtask` 处理），供实时聊天与批任务两条路径复用 |
| `src/components/ai-chat/SubtaskBubble.vue` | 递归渲染子代理委托气泡，惰性加载 |

**修改**

| 文件 | 改动 |
|---|---|
| `server/init_db.py` | 新增 `ai_chat_subtasks`/`ai_chat_subtask_messages` 两张表 |
| `server/utils/chat_persist.py` | `apply_event` 认识 `subtask` part，递归追踪子代理 sessionID 并持久化其消息 |
| `server/utils/batch_engine.py` | 轮询逻辑扩展到子代理 sessionID；改用 `opencode_parts.py` 的共享映射 |
| `server/routes/ai_chat.py` | 新增 `GET /ai/chat/sessions/:sid/subtasks/:subtaskId/messages` |
| `server/routes/ai_batch_admin.py` | 新增 `GET /ai/chat/admin/batches/:batchId/sessions/:sid/subtasks/:subtaskId/messages` |
| `server/utils/backup.py` | 两张新表加入备份/还原清单 |
| `src/views/ai-chat/AiChatView.vue` | 消息渲染循环里接入 `SubtaskBubble.vue` |
| `src/components/ai-chat/BatchConversationView.vue` | 同上 |
| `docs/user-guide/ai/assistant.md` | 说明子代理委托可展开查看 |
