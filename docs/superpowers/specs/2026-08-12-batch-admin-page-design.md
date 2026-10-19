# 批任务管理页（管理员）设计

**日期**：2026-08-12
**状态**：设计已确认，待编写实施计划

## 1. 目标与范围

给管理员一个**独立的批任务管理页面**，统一查看界面触发与 API 触发的全部批任务（跨用户），排查失败原因，并手动重试失败的子任务。

原始需求：

> 批任务，不管是从前台页面触发的，还是从 api 触发的，需要有一个正经的管理页面，要支持对失败的子任务做手动重试，要记录批任务及其子任务的当前状态。

### 1.1 已确认的范围决策

| 维度 | 决策 |
|---|---|
| 受众 | **纯管理员页面**。普通用户继续使用 AI 助手侧边栏的 `BatchGroup`，本页不改变他们的使用方式 |
| 位置 | **设置中心新增一条**，路由 `/admin/ai-batches` |
| 权限 | 复用现有 `admin.ai_chat_admin`，**不新建能力键** |
| 操作范围 | **查看 + 重试**（整批重试失败子任务、单条子任务重跑）。不提供删除、不提供改配置 |
| 对话可见性 | 管理员可打开子任务的**完整对话**，只读 |

### 1.2 明确不做（YAGNI）

- **不建状态历史表**。需求是「记录当前状态」，现有 `ai_chat_batches.status/total/done/failed` 与 `ai_chat_sessions.status/error_message` 已足够。
- **不做删除与改配置**。降低管理员误操作代价；这两项所有者在侧边栏仍可做。
- **对话查看器不做游标翻页**。先做有界截断（见 §4.3）。
- **不重构 `AiChatView.vue`**。见 §3.3 的取舍说明。

### 1.3 探索中发现的既有事实

这三条不是本设计引入的，但直接影响方案，记录备查：

1. **`admin.ai_chat_admin` 是一个悬空能力**：`server/utils/permissions.py:35` 定义了它（「AI 会话治理」），后端 `GET /ai/chat/admin/sessions` 与归档端点已存在，但**设置中心没有任何入口消费它**。本页面是它的第一个 UI 消费者。
2. **管理员读不到别人的子任务对话**：`routes/ai_chat.py:487` 的 `get_messages` 走 `_load_session_for_user(sid, userId)`，跨用户直接 404。因此「查看完整对话」**必须**新增管理员端点，无法复用。
3. **消息渲染内联在 `AiChatView.vue`**（1310 行）中，仓库里**没有**可复用的 `MessageList` 组件；独立的只有 `MarkdownView.vue`（160 行）。

## 2. 后端设计

### 2.1 新蓝图与端点

新建 `server/routes/ai_batch_admin.py`，蓝图 `ai_batch_admin_bp`，`url_prefix='/ai/chat/admin/batches'`。

**为什么独立成文件**：现有 `ai_chat_batches_bp` 的前缀被钉死在 `/ai/chat/batches`，装不下 `/ai/chat/admin/batches`；`ai_chat.py` 已 1096 行。更重要的是，这一层每个端点的安全假设与普通端点**相反**（跨用户查而非按归属过滤），物理隔离能让这个边界一眼可见。

5 个端点，全部挂 `@require_permission('admin.ai_chat_admin')`：

| 方法 | 蓝图内路径 | 说明 |
|---|---|---|
| GET | `` | 列表：跨全部用户，分页 + 筛选 |
| GET | `/<batchId>` | 详情：批任务 + 子任务清单 |
| GET | `/<batchId>/sessions/<sid>/messages` | 只读对话，按 `seq` 排序，有界截断 |
| POST | `/<batchId>/retry-failed` | 重试整批失败子任务 |
| POST | `/<batchId>/sessions/<sid>/reexecute` | 单条子任务重跑 |

**注册顺序**：必须在 `dynamic_bp`（catch-all `/<collection>`）**之前**注册，否则被其抢占。

### 2.2 数据层

`server/utils/batch_repo.py` 新增管理员作用域的**读**函数（命名如 `admin_list_batches` / `admin_get_batch_detail`），**不复用**现有的 `list_batches` / `get_batch_detail`。

**为什么不把现有函数的 `user_id` 改成可空**：现有函数第一个参数是必传的 `user_id`，语义是「按归属过滤」。改成可空后，一次手滑（忘传参数）会从「报错」变成「返回全部用户的数据」。这类默认值是安全事故的常见来源，宁可多两个函数名。

**写路径完全复用现有函数，不新增无作用域的变体。** 管理员重试的流程是：

1. 按 `batchId` 查出该批任务的**归属用户 id**（不按归属过滤的读）；
2. 用那个 `user_id` 调用现有的 `reset_failed_to_pending` / `reexecute_child`。

这样写路径上永远不存在「不按归属过滤」的函数。读路径泄漏的是可见性，写路径泄漏的是数据完整性，后者严重得多。

### 2.3 列表返回的新增字段

在现有批任务字段基础上增加两个管理场景必需的：

| 字段 | 来源 | 用途 |
|---|---|---|
| `ownerUsername` | JOIN `users` | 跨用户列表没有归属列无法使用 |
| `source` | `api_key_id IS NULL` → `'ui'`，否则 `'api'` | 直接对应需求里的「前台触发还是 API 触发」 |

**依赖备注**：`ownerUsername` 的 JOIN 依赖 `ai_chat_batches.user_id REFERENCES users(id)` 目前是 `NO ACTION` —— 即建过批任务的用户删不掉。这是一个**已知的既有缺陷**（记录在 `.superpowers/sdd/2026-08-11-ai-batch-open-api/progress.md`），本页面恰好依赖了它的副作用。日后若修复该外键（例如改为 `ON DELETE SET NULL`），本页面的归属列需要同步处理 NULL。

### 2.4 筛选与分页

| 筛选项 | 形式 |
|---|---|
| 状态 | 枚举，取值同 `ai_chat_batches.status` |
| 归属用户 | **用户名模糊匹配**（`users.username ILIKE`） |
| 来源 | 枚举 `ui` / `api` |
| 名称关键词 | 批任务名称模糊匹配 |

归属用户用模糊匹配而非下拉选择，是为了**避免耦合 `admin.users` 权限** —— 拉取用户列表需要那个能力，而本页面只要求 `admin.ai_chat_admin`，两者不必绑定。

分页沿用现有的 `page` / `pageSize`（上限 100）。

### 2.4.1 重试端点的行为约定（与既有实现的差异）

核实到的既有事实，实施时不要被它们带偏：

- **内部** `POST /ai/chat/batches/<id>/retry-failed`（`routes/ai_chat_batches.py:139`）**既无终态门也不返回 404** —— 批任务不存在时返回 `200 {"retried": 0}`。
- **对外** `POST /v1/ai-batches/<id>/retry-failed` 加了终态门（非终态返回 409）与 404。

**管理端点的选择**：

| 行为 | 决策 | 理由 |
|---|---|---|
| 批任务不存在 | **404** | 管理工具里静默返回 `{retried: 0}` 会让 `batchId` 打错看起来像「没有失败的子任务」 |
| 非终态批任务 | **允许重试，不设终态门** | `reset_failed_to_pending` 只动 `failed` 的子任务；一个仍在跑的大批任务里已经失败的几条，管理员应该能立刻重试而不必等整批结束。这与对外 API 的 409 不同 —— 对外那层是为了契约简单，管理场景的诉求相反 |
| 单条 `reexecute` 非终态 | **409** | 子任务本身正在跑，重跑会与运行中的会话冲突（见 §4.2） |

**不修改内部端点的现有行为**，它不在本次范围内。

### 2.5 审计

管理员的两个重试动作写入 `operation_logs`，`target_type='ai_chat_batch'`。管理员代替他人重跑任务却不留痕，出问题无法追溯是谁动的。与现有 AI 会话生命周期写 `operation_logs` 的做法一致。

### 2.6 捎带修正：消息排序

新的消息端点按 **`seq`** 排序，不用 `created_at`。

现有 `get_messages` 用 `ORDER BY created_at ASC`，而批任务子会话的消息由 worker 批量持久化、同事务内 `created_at` 相同，顺序不确定 —— 这与 `get_batch_results` 已修复的是同一个缺陷（`ai_chat_messages.seq` 列正是为此新增的）。

**老端点不动**：它服务用户自己的会话，改动面大且不在本次范围内。新端点从一开始就用对的。

## 3. 前端设计

### 3.1 入口

`src/views/admin/hub/settingsCatalog.ts` 的「AI 能力」组增加第 3 条：

```ts
{ id: 'ai-batches', label: 'AI 批任务', perm: 'admin.ai_chat_admin',
  component: () => import('@/views/admin/AiBatchAdmin.vue') }
```

侧边栏渲染、路由生成、权限判定全部从该目录自动派生，无需手写路由。

### 3.2 页面结构

**筛选栏 + 主表格 + 详情抽屉 + 对话弹窗**，最多叠一层。

- **筛选栏**：状态 / 归属用户 / 来源 / 名称关键词 + 刷新按钮
- **主表格列**：名称、归属用户、来源、状态标签、进度（`done`/`failed`/`total`）、Agent·模型、创建时间、完成时间、操作
- **详情用抽屉，不用内嵌展开行**：批任务 `prompt` 上限 20000 字符，加最多 50 个子任务，展开行会把分页表格撑得不可用。抽屉内含批任务摘要 + 子任务表（序号、文件名、状态、错误信息、最后消息预览）+ 每行「重跑」按钮，顶部「重试全部失败」
- **对话查看器用弹窗**，浮于抽屉之上；不做抽屉套抽屉

### 3.3 只读对话查看器（含取舍说明）

新建 `src/components/ai-chat/BatchConversationView.vue`，复用 `MarkdownView` 渲染文本，工具调用折叠展示。

**取舍**：不把 `AiChatView.vue`（1310 行）里的消息渲染抽成公共组件。拆它是一次风险不小的重构，动的是 AI 助手核心路径；且它内联的那段带着流式追加、编辑、重发等管理场景**必须没有**的能力，硬抽成公共组件等于让公共组件同时背两套语义。

**已知代价**：文本与工具调用的渲染逻辑有部分重复，消息结构若变更需改两处。此代价已被评估并接受。

### 3.4 状态管理

新增 `src/api/aiBatchAdmin.ts` 与 `src/stores/aiBatchAdmin.ts`，与用户态的 `src/stores/aiChatBatches.ts` **分开**。理由同后端：两者过滤语义相反，混用一个 store 会让「当前是谁的视角」变成隐式状态。

### 3.5 刷新策略

手动刷新按钮 + 条件轮询：**仅当当前页存在非终态批任务时**才轮询（10 秒间隔），全部终态即停止。管理页可能长时间开着无人查看，无条件轮询是白白消耗后端连接。

## 4. 边界情况与错误处理

### 4.1 并发重试

管理员与批任务所有者可能同时触发重试。不会双跑：`reset_failed_to_pending` 是把 `failed` 置 `pending` 的幂等 UPDATE，worker 通过 `FOR UPDATE SKIP LOCKED` 认领子任务。

### 4.2 `reexecute` 的终态门

`reexecute_child` 在子任务不是 `completed` / `failed` 时抛 `ValueError`。管理端点必须落成 **409**，不得裸 500。

### 4.3 对话响应体有界

批任务子会话持久化的是**完整对话含工具调用**，一次长 agent 运行可能有数百条消息、JSONB 体积可观。

消息端点默认返回**最近 500 条**（`ORDER BY seq DESC LIMIT 500`，返回前**反转为 `seq` 升序**，使前端拿到的始终是自然阅读顺序），响应含 `truncated: boolean` 与消息总数；弹窗在截断时明示「仅显示最近 500 条，共 N 条」。不做游标翻页 —— 先保证有界，确有需要再迭代。

### 4.3.1 `sessionId` 必须属于该 `batchId`

消息端点要校验 `sid` 确实是 `batchId` 下的子任务，不属于则 404。否则这个路径会变成一个「用任意 batchId 读任意会话」的通道 —— 虽然管理员本就有跨用户会话可见性，但让路径参数之间不自洽是一种会被后续改动放大的松散。

### 4.4 权限中途失效

管理员被撤销能力后接口返回 403。页面必须明确提示，**不能静默显示空列表** —— 空列表会被读作「没有批任务」。

### 4.5 批任务在操作期间被所有者删除

返回 404，页面提示并刷新列表。

## 5. 测试与验证

### 5.1 后端（pytest）

三条需要特别构造：

1. **权限门参数化遍历路由表**：遍历 `app.url_map` 中该前缀下的全部规则，逐个断言非管理员得 403。不挑单个端点测 —— 上个分支的审查发现过这个洞（`require_bound_key` 只有一个端点有测试，其余漏挂装饰器不会被发现）。新增端点忘挂装饰器时本测试立刻变红。
2. **重试的归属作用域**：管理员重试**他人**批任务时，断言 repo 收到的是**该批任务真实归属用户**的 id，而非管理员自己的。传错会表现为「重试没反应」，只断言 200 的测试完全抓不到。
3. **消息按 `seq` 排序**：构造一批 **`created_at` 完全相同、`seq` 乱序**的消息，断言返回按 `seq` 有序。改回 `ORDER BY created_at` 即变红。

其余：跨用户可见性、四个筛选各自生效、`reexecute` 对非终态子任务返回 409、`retry-failed` 对不存在的批任务返回 404（而非内部端点那样的 `200 {"retried": 0}`）、`retry-failed` 对**非终态**批任务**允许**执行（§2.4.1 的差异点，需要一条正向测试钉住，否则日后有人照抄对外 API 的 409 会悄悄改掉语义）、消息端点对不属于该批任务的 `sessionId` 返回 404、超过 500 条时 `truncated` 与总数正确且顺序为 `seq` 升序、管理员重试写入 `operation_logs`。

### 5.2 前端（vitest）

- 筛选参数拼装正确
- **轮询在全部终态时不启动**：显式推进定时器后断言请求数为 0（断言「不轮询」比断言「轮询」更易写成假绿）

### 5.3 Playwright（CLAUDE.md 强制）

admin 登录 → `/admin/ai-batches` → 看到**多个不同用户**的批任务 → 按状态筛选 → 打开详情抽屉 → 打开子任务对话 → 触发一次重试 → 断言渲染结果，并**回查数据库**确认状态真的变了（不只看 200 响应）。截图存入 `.playwright-mcp/`。

### 5.4 proxy.py 验证（CLAUDE.md 强制）

新路由形态。无 SSE、无自定义请求头，因此只需 `npm run build` → `python proxy.py` → 确认 5 个端点经 `:8080` 可达，且未被 `dynamic_bp` catch-all 抢占。

### 5.5 文档同步（CLAUDE.md 强制）

`docs/user-guide/ai/batch-tasks.md` 增加「管理员：批任务管理页」一节，含真实页面截图（存 `docs/user-guide/_images/`）。

## 6. 文件清单

**新建**

| 文件 | 职责 |
|---|---|
| `server/routes/ai_batch_admin.py` | 5 个管理员端点 |
| `server/tests/test_ai_batch_admin.py` | 后端测试 |
| `src/views/admin/AiBatchAdmin.vue` | 页面（筛选栏 + 表格 + 详情抽屉） |
| `src/components/ai-chat/BatchConversationView.vue` | 只读对话查看器 |
| `src/api/aiBatchAdmin.ts` | API 层 |
| `src/stores/aiBatchAdmin.ts` | 管理态 store |
| `src/stores/__tests__/aiBatchAdmin.test.ts` | 前端测试 |

**修改**

| 文件 | 改动 |
|---|---|
| `server/utils/batch_repo.py` | 新增管理员作用域的读函数 |
| `server/app.py` | 注册新蓝图（在 `dynamic_bp` 之前） |
| `src/views/admin/hub/settingsCatalog.ts` | 「AI 能力」组新增一条 |
| `docs/user-guide/ai/batch-tasks.md` | 新增管理员一节 |
| `CLAUDE.md` | 记录新页面与管理端点 |
