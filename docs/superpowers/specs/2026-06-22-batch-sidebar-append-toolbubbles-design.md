# 批任务：侧栏分组 + 批内追加 + 工具气泡入库 — 设计文档

> 状态：设计已与需求方逐项确认（去 tab 合并、任意状态可追加、工具气泡本期入库）。日期：2026-06-22

## 1. 目标

围绕 AI 批任务做三件相互独立、可分别交付的增强：

- **A 侧栏分组**：去掉「会话 / 批任务」双 tab，把批任务作为**可折叠分组**并入统一的「会话」列表；点组头展开看该批所有子任务，点子任务在主面板开其完整线程。
- **B 批内追加**：支持向**已存在的批次**追加新文件 → 新子会话，**任意状态**都可追加（追加后批回到 running，worker 自动接着跑）。
- **C 工具气泡入库**：批子会话持久化**完整对话**（含工具调用气泡），与交互式会话一致——目前批 worker 只存「prompt + 最终 text 答案」，工具调用 part 被丢弃。

## 2. 背景与现状

- 侧栏：`AiChatView` 有「会话 / 批任务」tab；`BatchListView`→`BatchDetailView`（批仪表盘 + 子任务表 + 重试/删除）。批子会话是真实 `ai_chat_sessions` 行（`batch_id`/`batch_seq`/`batch_input_file`），点击即开其线程。
- 持久化差异（已核实）：
  - 交互式 `utils/chat_persist.build_content` 保留 **text + tool_use** part（注释「tool_use parts kept so rendered results survive a reload」）→ 工具气泡入库。
  - 批 `utils/batch_engine._persist_conversation` 只存 user prompt + 最终 assistant **text**（`_OpenCodeFacade.list_messages` 只映射 `type=='text'`）→ **工具气泡不入库**。
- 前端 `AiContentPart` 已含 `tool_use` 类型且会话线程已渲染工具气泡 → **C 为纯后端改动**，落库后即显示。
- OpenCode REST `GET /session/<id>/message` 每条消息含 `parts`；**tool part 实测形状**：
  ```json
  {"type":"tool","callID":"...","tool":"read",
   "state":{"status":"completed","input":{...},"output":"...","title":""},
   "id":"...","messageID":"..."}
  ```

## 3. 关键决策（已确认）

| 决策 | 选择 |
|---|---|
| 分组形态 | 去掉「批任务」tab，全部并入「会话」列表的可折叠批次分组 |
| 追加状态 | 任意状态（completed/partial/failed/running）均可追加 |
| 工具气泡入库 | 本期纳入；仅对**新跑**生效，旧批不回填 |
| 拆分顺序 | C（纯后端）→ B（后端+小前端）→ A（前端重构），分 3 个 PR 递进 |

---

## 4. Part C：工具气泡入库（纯后端）

**文件**：`server/utils/batch_engine.py`；测试 `server/tests/test_batch_persist.py`（新）。

- 新增 `_content_from_parts(parts) -> list`：把 OpenCode 一条消息的 parts 映射为 typed content：
  - `text` 且非空 → `{'type':'text','text': p['text']}`
  - `tool` → `{'type':'tool_use', 'name': p.get('tool'), 'title': (p.get('state') or {}).get('title') or '', 'status': (p.get('state') or {}).get('status'), 'input': (p.get('state') or {}).get('input'), 'result': (p.get('state') or {}).get('output')}`
  - 其它（`step-start`/`step-finish`/`reasoning`）丢弃（对齐交互式 `build_content` 只留 text + tool_use）。
- 重写 `_persist_conversation(session_id, prompt, oc_session_id, directory)`：turn 结束后 `client.get_messages(oc_session_id, directory)` 读**全量消息**；落库：① 一条 `user` 消息（prompt）；② 每条 `assistant` 消息一行，content = `_content_from_parts(msg['parts'])`（空内容的助手消息跳过）。保持 best-effort、绝不抛。
  - 调用点 `_run_one`：把 `oc_session_id` + `directory` 传给 `_persist_conversation`（当前只传了 `final_msg`）。`_await_finished` 仍返回 preview 用于 `last_message_preview`。
- 不改前端（`tool_use` 气泡渲染已存在）。

**测试**：`_content_from_parts` 正确映射 text/tool、丢弃 reasoning/step；`_persist_conversation` 对「user + 含 tool_use 的多条 assistant」落库（mock `get_messages` 返回含 tool part 的消息，断言 `ai_chat_messages` 行数与 content 结构）。

---

## 5. Part B：批内追加（后端 + 小前端）

**文件**：`server/utils/batch_repo.py`、`server/routes/ai_chat_batches.py`；前端 `src/api/aiChatBatches.ts`、`src/stores/aiChatBatches.ts`、追加对话框组件；测试 `server/tests/test_batch_routes.py`、`test_batch_repo`（如有）。

- `batch_repo.append_to_batch(user_id, batch_id, files) -> dict`：
  - 校验 batch 属于 user（不存在/不属于 → 返回 None）；
  - `SELECT COALESCE(MAX(batch_seq), -1) FROM ai_chat_sessions WHERE batch_id=%s` → 起始 seq；
  - 插入 N 个 `pending` 子会话（seq 接续、`batch_input_file` = 各文件 path）；
  - `UPDATE ai_chat_batches SET total = total + N` 后调 `_recompute_batch_status`（terminal<total → running）；
  - 返回新子会话列表。
- 路由 `POST /ai/chat/batches/<id>/append`（`@login_required`）：body `{files:[{name,path}]}`（与 create 同形、同 `MAX_FILES_PER_BATCH` 上限校验，按**追加后总数**校验）；调 `append_to_batch`，404 if None；`get_worker().notify()`；返回 `{batch, sessions}` 或更新后的 detail。
- 前端：`appendBatch(id, files)` API；store action；组头「追加文件」→ 复用暂存上传（`stagingUpload`）的精简对话框（仅文件上传 + 提交）→ 调 append → 刷新该组。

**测试**：append 后 seq 接续、total+N、状态回 running、归属校验（他人 batch → 404）；超过 `MAX_FILES_PER_BATCH`（按总数）→ 400。

---

## 6. Part A：侧栏分组（前端重构）

**文件**：`src/views/ai-chat/AiChatView.vue`（去 tab、组合列表）、新增 `src/components/ai-chat/BatchGroup.vue`；复用 `stores/aiChatBatches.ts`。`BatchListView.vue`/`BatchDetailView.vue` 的职责并入分组（按需保留/精简或移除）。

- 去掉「会话 / 批任务」tab 切换；侧栏单一滚动区渲染：
  1. **普通会话**（非批，`listSessions` 现有；含 active/closed、close/reopen 按钮——保持上期会话治理 UI）；
  2. **批次分组**：每个批次一个 `BatchGroup`：
     - **组头**：批名 + 状态徽标 + `done/total` 进度 + agent/模型（小字，留空显示「默认」）+ 操作图标：**追加文件**、**重试失败**（`failed>0` 才显）、**删除**；点组头展开/折叠。
     - **展开体**：子会话按 `batch_seq` 列出，每项可点（`selectSession` 开线程）、显示状态点 + 文件名 + preview。
- 数据：批列表/详情/进度复用 `aiChatBatches` store 的现有轮询（列表 10s、详情 5s，后台暂停）；展开某组时确保已拉到其子会话。
- 主面板：点子会话沿用现有线程加载与渲染（含工具气泡，得益于 Part C）；批级信息集中在组头，不再需要独立 `BatchDetailView` 仪表盘（其重试/删除/追加动作迁到组头）。

**测试**：`vue-tsc` clean；分组渲染/折叠、组头操作触发对应 store action、tab 移除后无回归（既有批组件测试相应调整）。

---

## 7. 测试与验收

- 后端单测：`_content_from_parts`、`_persist_conversation`（多条含 tool_use）、`append_to_batch`（seq/total/状态/归属/上限）。批/扫描回归不破。
- 前端：`vue-tsc` clean；分组与追加对话框测试；既有 `CreateBatchDialog`/`aiChatBatches` 测试调整。
- 实测（8080 或 dev 栈）：
  - C：带工具调用的批子会话 → 点开能看到**工具气泡**且刷新后仍在；`ai_chat_messages` 含 `tool_use`。
  - B：对已完成批次**追加文件** → 新子任务接着跑、完成入库、组内出现。
  - A：侧栏无 tab，批次以可折叠组呈现,点组名展开看全部子任务。
- 文档：`docs/user-guide/ai/batch-tasks.md`（追加、分组、工具气泡可见）；`CLAUDE.md` 批段更新。

## 8. 风险与边界

- **C 仅新跑生效**：旧批不回填（历史 OpenCode 会话可能已被回收）。可接受。
- **tool part 形状漂移**：映射基于实测 OpenCode 形状；若版本升级字段变动，`_content_from_parts` 做防御性 `.get`，缺字段降级为空。
- **A 重构面较大**：分组复用现有 batch store 轮询，避免新增后端；`BatchDetailView` 移除前确认无其它引用。
- **降级一致性**：持久化全程 best-effort，不影响 worker 主流程（与现状一致）。
- **拆分**：三块各自独立成 PR（C→B→A），任一可单独上线。
