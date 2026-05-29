# AI 助手会话工作目录隔离 (SP1) — 设计

- 日期: 2026-05-29
- 状态: 已批准（待实现）
- 范围: 后端 OpenCode 客户端 + AI chat 路由(小改动)

## 背景与问题

AI 助手里调用的 skill / agent 通过 OpenCode 内置工具(`bash`/`write`/`edit`，含 `git clone`)创建/修改文件。
实测:这些文件落在 **OpenCode 的启动 cwd(仓库根目录)**,而不是会话的隔离 workspace
(`~/.check-manage/ai-workspaces/<user>/<session>`)——多会话共用一个目录、还会污染服务端工作树。

最初考虑"每会话起一个 `opencode serve`(cwd=workspace)"来隔离,但太重(每会话一个进程、端口、生命周期)。

## 关键发现(已在运行中的 OpenCode 上验证)

OpenCode HTTP API 的 `/session/*` 系列端点(含 `prompt_async`、`shell`、`message`、`diff` …)都接受
**`directory` query 参数**,且它**决定该次工具执行的工作目录**:

- 实测:`POST /session/{id}/shell?directory=<X>` 返回的 `info.path.cwd == <X>`(传入的临时目录),
  而非 server 启动 cwd。
- 我们之前文件落在仓库根目录,根因是 `send_prompt_async` **没有传 `directory`**,OpenCode 回退到 server 启动 cwd。

⇒ 单个共享 `opencode serve` 即可做到"每会话工作目录隔离",只需在触发工具的请求(prompt)上带
`directory=<该会话 workspace>`。无需每会话进程。

## 目标 / 非目标

**目标**
- 让某会话内 agent/skill 的所有内置工具文件操作都发生在**该会话的 workspace** 下,会话间隔离,
  且不污染服务端启动目录。
- 改动小、保留现有单一共享 `opencode serve` 架构。

**非目标**
- 不引入每会话 OpenCode 进程 / 端口 / 生命周期管理。
- 不做变更文件的采集与渲染(那是 SP2:`docs/.../specs/<date>-ai-chat-changed-files-*` 另立,
  可直接用 OpenCode 原生 `GET /session/{id}/diff` → `FileDiff[]`)。
- 不改 MCP 工具(它们本就用会话 workspace 作 cwd)。

## 设计

### 1. `server/utils/opencode_client.py` — `send_prompt_async` 增加 `directory`

`send_prompt_async(opencode_session_id, content, model="", directory="")`:当 `directory` 非空时,
在 POST `/session/{id}/prompt_async` 上带 `params={"directory": directory}`。其余不变(body 仍是
`{parts:[{type:text,text}]}` + 可选 `model`)。

### 2. `server/routes/ai_chat.py` — `send_message` 传入会话 workspace

`send_message` 已经能从 `_load_session_for_user` 拿到 `workspace_path`(`sess[4]`)。
把现有调用改为:
```python
OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(
    sess[2], prompt.strip(), model=OPENCODE_MODEL, directory=sess[4],
)
```
(`create_session` 已在 `?directory=` 传 workspace 作为会话默认;prompt 再带一次,确保每轮工具 cwd=workspace。)

> 说明:`directory` 是绝对路径(`create_session_workspace` 已 resolve);`requests` 负责 URL 编码。
> SSE/事件流不受影响(仍走全局 `/event` 总线按会话过滤),无需改动。

## 数据流

1. 用户发消息 → `send_message`。
2. 后端给 OpenCode `prompt_async?directory=<workspace>`。
3. OpenCode 这一轮 agent 的所有工具调用(bash/write/edit/git clone …)以 `cwd=<workspace>` 执行 →
   文件落在该会话 workspace。
4. 不同会话 workspace 不同 → 文件系统隔离;也不再写到服务端启动目录。

## 测试

- **单测 `server/tests/test_opencode_client.py`**:`send_prompt_async(..., directory="/ws")` 时,
  请求 URL/params 含 `directory=/ws`;`directory` 为空时不带该参数(向后兼容)。
- **单测 `server/tests/test_routes_ai_chat.py`**:`send_message` 调用 `send_prompt_async` 时
  传入的 `directory` 等于该会话的 `workspace_path`。
- **真机验证**:在一个会话里让 agent `git clone <小型公开仓库>` 并新增/修改文件,确认文件落在
  **该会话 workspace**(不在仓库根);第二个会话看不到第一个会话的文件。

## 风险与缓解

- **OpenCode 版本差异**:本设计依赖 `directory` query 参数生效(已在当前运行版本实测 `/shell` 的 cwd)。
  若升级后行为变化,真机验证会立即暴露。`prompt_async` 与 `shell` 同属带 `directory` 的端点,行为预期一致
  (实现首步即真机验证 clone 落点)。
- **绝对路径/编码**:workspace 为绝对路径,`requests` params 自动编码;OpenCode 需要可访问该目录(已存在)。
- 改动面极小(2 个函数),回归风险低。

## 影响文件清单

- `server/utils/opencode_client.py`(`send_prompt_async` 增参 + 传 query)
- `server/routes/ai_chat.py`(`send_message` 传 `directory=sess[4]`)
- 测试:`server/tests/test_opencode_client.py`、`server/tests/test_routes_ai_chat.py`
