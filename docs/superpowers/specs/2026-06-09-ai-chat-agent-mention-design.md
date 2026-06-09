# AI Chat — `@` 子智能体 mention 补全 设计

- 日期：2026-06-09
- 状态：已评审待实现
- 关联：AI Agent Chat（`server/routes/ai_chat.py`、`server/utils/opencode_client.py`、`src/views/ai-chat/AiChatView.vue`、`src/components/ai-chat/CommandPalette.vue`、`src/stores/aiChat.ts`、`src/api/aiChat.ts`）；已合并的「primary agent 选择器」（`/ai/chat/agents`、`agentBySession`）

## 背景与目标

OpenCode CLI 里输入 `@` 会弹出 **agent 列表**（截图实测为 `@general`/`@explore`/`@developer`/`@reviewer`，均为 subagent），选中后在消息里**点名/委派某个子智能体**。当前 aichat 不支持。本设计给 aichat 输入框加 `@` 子智能体 mention 补全。

与已有「primary agent 选择器」互补：**Tab/下拉切主 agent（build/plan）；`@` 在消息里点名 subagent**。

### OpenCode `@` 的真实语义（已核实其 OpenAPI schema + 实测）

- `@` 触发 **agent 补全**（列出 subagent）。选中插入 `@<name>` 文本。
- prompt 的 `parts` 数组接受 `TextPartInput | FilePartInput | AgentPartInput | SubtaskPartInput`。`@name` 产生一个 **`AgentPartInput`**：
  ```json
  { "type": "agent", "name": "general",
    "source": { "value": "@general", "start": 12, "end": 20 } }
  ```
  - 必填仅 `type`、`name`；`source`（mention 在文本中的值与字符偏移）**可选**，本设计带上以保真。
- 即：一条带 `@general` 的消息会发送 `parts = [TextPart(全文), AgentPart(name=general,...)]`，OpenCode 据此在该轮点名委派子智能体。

### 范围

- `@` **只补全 subagent**（`mode == 'subagent'`，如 general/explore）。primary（build/plan）由已有 Tab/下拉处理，不进 `@`。
- 作用于普通消息发送路径（`POST /ai/chat/sessions/:id/messages` → `send_prompt_async`）。
- slash `/` 命令 palette 保持不变；`@` 是并列的另一种补全。

### 非目标（YAGNI）

- 不支持 `@` 文件引用（OpenCode 此构建的 `@` 是 agent，不是文件）。
- 不做 SubtaskPart、不做 mention 的富文本高亮（纯文本 `@name` + 发送时解析）。
- 不在编辑期实时追踪偏移；偏移在**发送时**一次性扫描计算。

## 架构与数据流

```
用户在输入框打 @gen
  → AiChatView 检测光标前的 @token（query="gen"）
  → 弹出 mention palette（subagents 过滤）
  → ↑↓ 选择，Enter/Tab 接受 → 文本替换为 "@general "（光标处）
发送时：
  → sendUserMessage 扫描全文 @<name>（name ∈ 已加载 subagents）
     生成 agentMentions=[{name, value:"@general", start, end}]
  → sendMessage(sid, content, paths, model, agent, agentMentions)
  → 后端 send_message 读 body.agentMentions
  → send_prompt_async(parts = [TextPart(content), AgentPartInput(每个 mention)])
  → OpenCode 收到 agent parts，点名委派 subagent
```

## 后端改动

### `server/routes/ai_chat.py` — `GET /ai/chat/agents` 扩展

当前返回 `{ agents:[{name,description}](primary), default }`。**新增 `subagents` 字段**（向后兼容，不动 `agents`/`default`，已合并的 primary 选择器无需改）：

```python
subagents = [
    {'name': a.get('name'), 'description': a.get('description') or ''}
    for a in (raw or [])
    if a.get('mode') == 'subagent' and a.get('name') not in INTERNAL_AGENTS
]
# 响应：{'agents': agents, 'subagents': subagents, 'default': default}
```

### `server/utils/opencode_client.py` — `send_prompt_async` 支持 agent parts

签名加 `agent_parts: list | None = None`。构造 body 时，在已有 text part 之后追加 AgentPartInput：

```python
def send_prompt_async(self, opencode_session_id, content, model="", directory="",
                      agent="", agent_parts=None):
    parts = [{"type": "text", "text": content}]
    for m in (agent_parts or []):
        name = (m.get('name') or '').strip()
        if not name:
            continue
        part = {"type": "agent", "name": name}
        src = m.get('source')
        if isinstance(src, dict) and all(k in src for k in ('value', 'start', 'end')):
            part["source"] = {"value": src['value'], "start": src['start'], "end": src['end']}
        parts.append(part)
    body = {"parts": parts}
    if model and "/" in model:
        provider_id, model_id = model.split("/", 1)
        body["model"] = {"providerID": provider_id, "modelID": model_id}
    if agent:
        body["agent"] = agent
    ...（directory/params/POST 不变）
```

（保留现有 `model`/`agent`/`directory` 逻辑；仅把单一 text part 改为 `parts` 列表并追加 agent parts。）

### `server/routes/ai_chat.py` — `send_message` 读取 mentions

在读 `requested_agent` 旁：

```python
agent_mentions = body.get('agentMentions')
if not isinstance(agent_mentions, list):
    agent_mentions = []
OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(
    sess[2], prompt.strip(), model=effective_model, directory=sess[4],
    agent=requested_agent, agent_parts=agent_mentions,
)
```

响应体可附带 `'agentMentions': [m['name'] for m in agent_mentions]`（便于测试/前端确认）。

## 前端改动

### `src/api/aiChat.ts`

- `listAgents()` 返回类型加 `subagents: AgentInfo[]`。
- `sendMessage(id, content, attachments, model, agent, agentMentions = [])`：body 加 `agentMentions`。类型：
  ```typescript
  export interface AgentMention { name: string; value: string; start: number; end: number }
  ```

### `src/stores/aiChat.ts`

- 新增 state `subagentsBySession: Record<string, AgentInfo[]>`（或一个简单的 `subagents: AgentInfo[]`，因 subagent 列表与会话无关，可全局缓存一次）。**决定：全局 `subagents: AgentInfo[]`**（agent 配置与会话无关，简化）。
- `sendUserMessage`：发送前扫描 `content` 生成 `agentMentions`（见下「mention 解析」），传给 `sendMessage`。

### mention 解析（纯函数，可单测）— `src/utils/agentMentions.ts`

```typescript
import type { AgentMention } from '@/api/aiChat'
// 扫描文本中所有 @<name>（name 由字母数字/-/_ 组成），保留 name ∈ knownNames 的，
// 记录 value="@name" 与字符偏移 [start,end)。
export function parseAgentMentions(text: string, knownNames: Set<string>): AgentMention[] {
  const out: AgentMention[] = []
  const re = /(^|\s)@([A-Za-z0-9_-]+)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    const name = m[2]
    if (!knownNames.has(name)) continue
    const start = m.index + m[1].length      // '@' 的位置
    out.push({ name, value: '@' + name, start, end: start + name.length + 1 })
  }
  return out
}
```

### `@` token 检测（纯函数，可单测）— 同 `agentMentions.ts`

```typescript
// 找光标左侧正在输入的 @token：@ 必须位于行首或空白后，@ 与光标间无空白。
// 返回 { query, start, end }（start=@ 位置，end=光标），无则 null。
export function activeMentionToken(text: string, cursor: number):
  { query: string; start: number; end: number } | null {
  let i = cursor - 1
  while (i >= 0 && /[A-Za-z0-9_-]/.test(text[i])) i--
  if (i < 0 || text[i] !== '@') return null
  if (i > 0 && !/\s/.test(text[i - 1])) return null
  return { query: text.slice(i + 1, cursor), start: i, end: cursor }
}
```

### `src/views/ai-chat/AiChatView.vue` — 补全 UI

- 拉取：`fetchAgents()`（已有）把 `r.subagents` 存入 store `subagents`。
- 维护 `cursorPos`（textarea `@select`/`@click`/`@keyup`/`@input` 更新 `selectionStart`）。
- 新增 `mentionPalette` computed：用 `activeMentionToken(input, cursorPos)`，命中则 = store.subagents 按 query 过滤；否则 `[]`。
- **palette 合一**：`/` 命令 palette 与 `@` mention palette 互斥（一个看行首 `/`，一个看光标 `@token`）。`paletteOpen` = 任一非空；`activeIndex`/↑↓/Enter/Tab 流程复用；`acceptItem` 按来源分流：命令 → 现有逻辑；mention → 把 `[start,end)` 替换为 `@name `（并更新 cursorPos）。
- 复用 `CommandPalette.vue`：给它加 `prefix?: '/' | '@'` prop（默认 `/`），渲染 `{{prefix}}{{name}}`；`PaletteItem['kind']` 增加 `'agent'`，`groupLabel` 加 `agent:'智能体'`。

## 错误处理

- 无 subagent / 拉取失败：`@` palette 为空，不弹出；不影响发送与 `/` palette。
- 文本里 `@xxx` 不匹配任何 subagent：不生成 mention（当普通文本发送，OpenCode 也只当文本）。
- 发送时 mention 解析在前端完成；后端对 `agentMentions` 做防御性校验（非 list → 视为空；item 缺 name → 跳过）。
- 偏移与发送的 TextPart 文本一致（同一 `content` 扫描），`source` 可选，缺失也不影响 OpenCode 识别 agent。

## 测试

- **前端纯函数 `src/utils/__tests__/agentMentions.test.ts`**：
  - `activeMentionToken`：行首 `@`、空白后 `@`、句中 `@`、`@` 前非空白（不触发）、已带空格（不触发）、光标在 token 中间。
  - `parseAgentMentions`：单个/多个 mention、未知 name 跳过、`@` 前需空白、偏移正确（value/start/end）。
- **前端 store**：`sendUserMessage` 在文本含已知 `@subagent` 时，把 `agentMentions` 透传给 `sendMessage`（mock 断言第 6 参）。
- **后端 `server/tests/test_routes_ai_chat.py`**：
  - `GET /ai/chat/agents` 返回 `subagents`（mode==subagent 项），且 `agents` 仍只含 primary。
  - `send_message` 带 `agentMentions` 时，`send_prompt_async` 收到 `agent_parts`，且构造的 parts 含 `{type:'agent', name:...}`（可对 `send_prompt_async` 做单测：给定 agent_parts，断言 body.parts 结构）。

## 影响文件清单

修改：
- `server/routes/ai_chat.py`（`/ai/chat/agents` 加 `subagents`；`send_message` 读 `agentMentions`）
- `server/utils/opencode_client.py`（`send_prompt_async` 加 `agent_parts` + 构造 agent parts）
- `src/api/aiChat.ts`（`AgentMention` 类型、`listAgents` 加 `subagents`、`sendMessage` 加 `agentMentions`）
- `src/stores/aiChat.ts`（`subagents` 缓存、`sendUserMessage` 解析并透传 mentions）
- `src/views/ai-chat/AiChatView.vue`（cursorPos、mention palette、acceptItem 分流、fetch subagents）
- `src/components/ai-chat/CommandPalette.vue`（`prefix` prop、`agent` kind）

新增：
- `src/utils/agentMentions.ts`（`activeMentionToken` + `parseAgentMentions`）
- `src/utils/__tests__/agentMentions.test.ts`
