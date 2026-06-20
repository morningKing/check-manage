# AI 会话长期记忆与韧性恢复 — 设计文档

> 状态：设计已与需求方逐节确认，待评审。
> 日期：2026-06-20

## 1. 目标

给 AI 助手会话加上**跨会话的长期记忆**（用 `mem0ai` 管理），并让旧会话在底层 OpenCode session 失效后仍能**被重新调用、无缝继续**。最终效果：助手"记得"用户的偏好/习惯/事实，新会话自动调用这些记忆；旧会话即使 OpenCode 进程重启过也能复活继续。

## 2. 背景与现状

- 会话与消息**已经持久化**：`ai_chat_sessions`（含 `title`/`status`/`opencode_session_id`/`workspace_path`/`user_id`/`last_message_preview` 等）、`ai_chat_messages`（`content` JSONB）。
- 基础会话管理**已存在**：列表、重命名、打开看历史、复用仍存活的 `opencode_session_id` 继续（`server/routes/ai_chat.py`）。
- **缺口**：
  1. 没有任何跨会话的长期记忆机制；
  2. "重新调用"依赖 OpenCode 进程里的 session 还活着，OpenCode 一重启旧 session 上下文即断。
- **环境约束**：`mem0ai` 未安装；PostgreSQL 的 `pgvector` 扩展当前不可用。

## 3. 关键决策（已确认）

| 决策 | 选择 |
|---|---|
| 记忆作用域/归属 | **按用户全局**（mem0 `user_id`，跨会话/跨项目） |
| 向量存储后端 | **Chroma 嵌入式**（纯 Python，本地磁盘持久化，零额外服务） |
| 集成方式 | **两者结合**：Flask 被动自动注入+抽取（基线）+ MCP 主动记忆工具（agent 精修） |
| 会话恢复 | **韧性恢复**：OpenCode session 失效时用「历史+记忆」重建并继续 |
| LLM / embedder | **复用现有 DashScope/Qwen 的 OpenAI 兼容端点**（默认；admin 可改） |

## 4. 架构总览与数据流

新增/改动单元（各自单一职责、可独立测试）：

| 单元 | 文件 | 职责 |
|---|---|---|
| 记忆集成层 | `server/utils/memory.py`（新） | 封装 mem0 client（Chroma + DashScope LLM/embedder），对外暴露 add/search/list/delete |
| 被动注入+抽取钩子 | 改 `server/routes/ai_chat.py` | 转发前 `search` 注入；`message.finished` 落库后 `add` 抽取 |
| MCP 记忆工具 | 改 `mcp-server/` + Flask 内部端点 | `memory_search`/`memory_add`/`memory_delete`，经 Flask 内部 API 走（见 §7） |
| 会话韧性恢复 | 改 `server/routes/ai_chat.py` | OpenCode session 失效 → 重建 + 注入历史+记忆 |
| 记忆管理 | 新 API + 前端小页 | 用户查看/删除自己的长期记忆 |

**一轮对话的数据流：**
```
用户发消息
  └─ Flask: search_memory(user_id, 消息)  → Top-K 相关记忆
       └─ 拼进发给 OpenCode 的 prompt（被动注入，不进 ai_chat_messages）
            └─ OpenCode agent 运行（期间可主动调 MCP memory_search/add 精修）
                 └─ SSE message.finished → persist_turn 落库
                      └─ Flask（后台线程）: add_memory(user_id, [user, assistant]) 抽取/更新记忆
```
记忆作用域 = `ai_chat_sessions.user_id`。Chroma 持久化目录在仓库外：`~/.check-manage/mem0/`（env `MEM0_STORE_ROOT` 可改；不进 git、不被 agent 文件工具够到）。

## 5. 记忆集成层（`server/utils/memory.py`）

唯一对外接口（其余代码只调它）：
```
get_memory()                           -> Memory | None   # 单例；未启用/初始化失败返回 None
add_memory(user_id, messages: list)    -> None            # 抽取并存（后台执行，不阻塞响应）
search_memory(user_id, query, limit=5) -> list[dict]      # 检索相关记忆（同步，带超时）
list_memories(user_id)                 -> list[dict]      # 管理页用
delete_memory(memory_id)               -> None
```

mem0 配置（一个 config dict 装配三件套）：
- `vector_store`: chroma → `path = ~/.check-manage/mem0/`，collection `memories`
- `llm`: openai 兼容 → `base_url` + `api_key` 复用 `ai_settings`；model = `ai_settings.model`（qwen-plus）
- `embedder`: openai 兼容 → 同上 base_url/key；model = 新增配置 `embedding_model`，默认 `text-embedding-v3`

`base_url` 由 `ai_settings.endpoint` 去掉 `/chat/completions` 派生（→ `.../compatible-mode/v1`）。

**新增配置项**（挂在 `ai_settings`，admin 的 AI 设置页可调）：`mem0_enabled`（总开关）、`embedding_model`。

**依赖**：`mem0ai`、`chromadb` 加入 `server/requirements.txt`。

### 两条硬性隔离原则
1. **降级安全**：`mem0_enabled=false` 或 mem0 初始化/调用失败 → 所有记忆操作 no-op / 返回空；**AI 聊天完全不受影响**，绝不因记忆层异常中断对话。
2. **异步抽取**：`add_memory` 调 LLM 抽取（秒级），走**后台线程**（复用现有线程基础），不阻塞 SSE 落库。`search_memory` 同步但带超时（默认 1.5s），失败降级为"无记忆注入"。

线程安全：模块级单例 + 锁（配合已上线的并发 serving）。

## 6. 被动自动注入 + 抽取（Flask 网关）

接点（`server/routes/ai_chat.py`）：
- **注入** —— `send_message` 构建 `prompt`（`_AGENT_DIRECTIVE + content` + 附件/导出提示）之后、`OpenCodeClient.send_prompt_async` 之前：
  - `mems = search_memory(user_id, content, limit=5)` → 渲染成简短段落：
    ```
    [关于当前用户的长期记忆（供参考，不必逐条复述）]
    - …
    - …
    ```
    放在 `_AGENT_DIRECTIVE` 之后、用户 `content` 之前。
  - **只进发给 agent 的 prompt，不写入 `ai_chat_messages`**：用户可见历史保持原样。
  - 空/失败/超时 → 不注入。
- **抽取** —— SSE `message.finished` 的 `persist_turn` 落库之后：取这一轮 `[user content, assistant text]` → `add_memory(user_id, messages)` 走后台线程；增/改/删由 mem0 内部用 LLM 自行决定。

边界：
- 严格按 `user_id` 作用域，绝不跨用户注入。
- 注入预算 K=5（可配），段落简短，防 prompt 膨胀。
- **batch / ai_scan 等非交互会话**（`batch_id`/`scan_task_id` 非空）默认**不注入也不抽取**——它们不是个人对话，避免污染用户记忆。

## 7. MCP 主动记忆工具（mcp-server）

工具：`memory_search(query, limit)`、`memory_add(text)`、`memory_delete(id)`，经现有 `token→user` 映射作用域到该用户。

**关键决策 —— 避免双进程同写 Chroma**：MCP server 是独立进程（独立 venv）。若它也直接持有 mem0，会与 Flask **双进程同写同一 Chroma（SQLite 单写后端）**，易损坏。因此：
- **Flask 独占 mem0/Chroma**（写串行化、单一来源）；
- MCP 工具通过 **Flask 内部 HTTP 端点** `/ai/memory/internal/{search,add,delete}`（带 token 鉴权，仅供 MCP 调）转发；
- MCP server 本就连 DB 验 token，加几个转发工具即可。

## 8. 会话韧性恢复

- **触发**：`send_message` 调 OpenCode 时，若 `opencode_session_id` 失效（进程重启/过期 → 返回错误/404）。
- **恢复**：捕获失效 → `create_session`（复用旧 `workspace_path`）→ 注入「**最近 N 轮历史原文 + mem0 记忆**」作为新 session 初始上下文 → 更新 `ai_chat_sessions.opencode_session_id` → 重发当前消息。对用户表现为"无缝复活"。
- **YAGNI**：历史用**最近 N 轮原文**（不额外做 LLM 摘要）；配合 mem0 记忆已足够，超长再迭代。
- N 默认值在实现时定（如 6 轮），可配。

## 9. 记忆管理 UI

- API：`GET /ai/memories`（`list_memories(user_id)`）、`DELETE /ai/memories/:id`（校验归属当前用户）。
- 前端：AI 助手里"我的长期记忆"抽屉/小页，列出 + 可删除（透明、可纠错）。

## 10. 分阶段交付（YAGNI，可增量上线）

- **M1**：记忆集成层 + 被动注入/抽取 + 配置/依赖 + 连通性验证（最高价值，记忆"自动形成与调用"先跑起来）。
- **M2**：MCP 主动记忆工具（Flask 内部端点 + mcp-server 工具）。
- **M3**：会话韧性恢复。
- **M4**：记忆管理 UI。

## 11. 测试 / 验收

- **连通性（M1 第一步）**：最小脚本验证 mem0 + Chroma + DashScope 能真 `add`/`search` 一条；并确认 `mem0ai`+`chromadb` 在 Windows/py3.12 可安装。
- **单元**：
  - `memory.py`：mock mem0 → 降级 no-op、作用域、异步 add；
  - 注入/抽取钩子：mock `search`/`add` → 注入段拼接正确、非交互会话跳过；
  - 韧性恢复：mock OpenCode 失效 → 触发重建 + 注入历史/记忆；
  - MCP 工具：mock Flask 内部端点 → 鉴权 + 作用域。
- **降级验收**：`mem0_enabled=false` 时聊天完全正常（无记忆调用、无报错）。
- **文档**：用户指南新增「AI 长期记忆」（`docs/user-guide/ai/`）；CLAUDE.md「AI Agent Chat」段同步。

## 12. 风险与验证点（诚实标注）

- **DashScope 作 mem0 embedder/LLM 的兼容性**：mem0 用 OpenAI provider 指向 DashScope 兼容端点，需验证 embedding 维度与 `/embeddings` API 兼容性。M1 连通性脚本先打通，否则退路：本地 `sentence-transformers` embedder。
- **依赖体积**：`chromadb` 会带 onnxruntime 等；M1 先确认可安装与启动开销。
- **Chroma 单写**：已通过"Flask 独占 mem0"规避双进程写冲突（§7）。
- **记忆质量**：mem0 抽取依赖 LLM，可能产生噪音记忆；记忆管理 UI（M4）提供人工删除作为纠错兜底。
