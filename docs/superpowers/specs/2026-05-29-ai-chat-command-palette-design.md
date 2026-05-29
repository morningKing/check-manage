# AI 助手输入框命令提示 + 执行（OpenCode 命令/技能）— 设计

- 日期: 2026-05-29
- 状态: 已批准（待实现）
- 范围: 前端（命令注册表 + CommandPalette 组件 + AiChatView 接线 + store）+ 后端（列命令/技能 + 运行命令 + opencode_client）。

## 背景

输入框目前只拦截前端命令 `/mcps`（见 [[ai-chat-mcp-services-list-design]]）。用户希望输入 `/` 时弹出命令提示下拉，列出可用命令并能**真正执行** OpenCode 原生命令，以及发现/使用技能。

### 关键发现（已验证）

- OpenCode HTTP API（v 当前）暴露：
  - `GET /command` → list[28]，每项 `{name, description, source, template}`（如 `init`）。
  - `GET /skill` → list[26]，每项 `{name, description, location, content}`（如 `clawhub`）。
  - `POST /session/{sessionID}/command`，body `{command, arguments?, agent?, model?, parts?, ...}` —— **运行一个命令**（等同一次 prompt 回合）。
  - **没有**“运行技能”的接口；技能由 agent 在回合内通过 skill 工具自行调用。
- 现有 SSE 代理按 `directory=workspace` scope（见 token 修复后的 `subscribe_events(directory=)`）。命令回合必须在该流上产出 message 事件 —— **实现首个任务先真机验证**。
- 现有常量：`OPENCODE_BASE_URL`、`OPENCODE_MODEL`、`MCP_NAME`；`_load_session_for_user` 返回 `(id,user_id,opencode_session_id,status,workspace_path)`；前端 `fileUrl`/`downloadFileUrl`；`/mcps` 拦截在 `AiChatView.send()`，调 `store.showMcpServices()`。

## 目标 / 非目标

**目标**：输入 `/` 弹出命令下拉（内置前端命令 + OpenCode 命令 + 技能），键盘可导航；选中：
- 内置/OpenCode 命令 → 填入 `/<name> ` 等你补参数，回车执行（OpenCode 命令经后端 `POST /command`，输出走现有 SSE）。
- 技能 → 插入脚手架「使用 \`<name>\` 技能:」，回车按普通消息发送，agent 自行调用。

**非目标**：
- 不做命令参数表单/校验、不做模糊排序算法（简单 `startsWith`/`includes`）。
- 不做技能直接运行（OpenCode 无此接口）。
- 不改 OpenCode。
- 不做命令历史/收藏（YAGNI）。

## 设计

### 三类条目语义

| 条目 | 来源 | 选中行为 | 回车行为 |
|------|------|----------|----------|
| 内置前端命令（`/mcps`、`/mcp`） | 前端注册表 | 填入 `/<name> ` | 跑注册表 `run()` |
| OpenCode 命令（`/init` 等） | `GET /command` | 填入 `/<name> ` | `POST /command {command,arguments}`，SSE 流出 |
| 技能（`clawhub` 等） | `GET /skill` | 把输入替换为脚手架「使用 \`<name>\` 技能:」并聚焦末尾 | 普通 `sendUserMessage`（agent 调技能） |

### 1. 前端命令注册表 `src/components/ai-chat/chat-commands.ts`

```ts
import type { useAiChatStore } from '@/stores/aiChat'
export interface FrontendCommand {
  name: string          // 不含斜杠，如 'mcps'
  description: string
  run: (store: ReturnType<typeof useAiChatStore>) => void | Promise<void>
}
export const FRONTEND_COMMANDS: FrontendCommand[] = [
  { name: 'mcps', description: '列出已配置的 MCP 服务及工具', run: (s) => s.showMcpServices() },
]
// '/mcp' 作为 '/mcps' 的别名在 send() 解析时处理（不单列一项，避免重复）
export function findFrontendCommand(name: string): FrontendCommand | undefined {
  const n = name.toLowerCase()
  return FRONTEND_COMMANDS.find(c => c.name === n) || (n === 'mcp' ? FRONTEND_COMMANDS.find(c => c.name === 'mcps') : undefined)
}
```

### 2. 组件 `CommandPalette.vue`

- props：`items: PaletteItem[]`、`activeIndex: number`。`PaletteItem = { kind: 'builtin'|'command'|'skill'; name: string; description: string }`。
- 纯展示：按 `kind` 分三组（标题「内置 / 命令 / 技能」），高亮 `activeIndex` 对应项，emit `select(item)`（点击）。
- 空 `items` 时不渲染（父组件控制显隐）。
- scoped 样式：绝对定位浮层、分组标题、高亮行、`<code>` 命令名 + 次要色描述。

### 3. 后端

**`server/utils/opencode_client.py`** 新增：
```python
def list_commands(self, directory: str = "") -> list:
    params = {"directory": directory} if directory else None
    r = requests.get(self._url("/command"), params=params, timeout=self.timeout)
    r.raise_for_status(); return r.json()

def list_skills(self, directory: str = "") -> list:
    params = {"directory": directory} if directory else None
    r = requests.get(self._url("/skill"), params=params, timeout=self.timeout)
    r.raise_for_status(); return r.json()

def run_command(self, opencode_session_id, command, arguments="", model="", directory=""):
    body = {"command": command, "arguments": arguments}
    if model and "/" in model:
        p, m = model.split("/", 1); body["model"] = {"providerID": p, "modelID": m}
    params = {"directory": directory} if directory else None
    r = requests.post(self._url(f"/session/{opencode_session_id}/command"), params=params, json=body, timeout=self.timeout)
    r.raise_for_status()
```
（`run_command` 的 model 形态需在实现首任务核对：command body 的 `model` 可能要 string 而非 `{providerID,modelID}`——以真机为准；先按 prompt 的对象形态，验证后调整。）

**`server/routes/ai_chat.py`** 新增：
- `GET /sessions/:id/commands`（`@login_required`，归属校验）→ 返回
  `{'commands': [{'name','description'} for c in list_commands(ws)], 'skills': [{'name','description'} for s in list_skills(ws)]}`；任一来源失败→该列表为 `[]`（非致命）。
- `POST /sessions/:id/command`（`@write_required`，归属校验）→ 读 body `{command, arguments}`，调
  `OpenCodeClient(...).run_command(sess[2], command, arguments, model=OPENCODE_MODEL, directory=sess[4])`，置会话 streaming 语义同 `send_message`（assistant 消息由现有 SSE `generate()` 持久化），返回 `202`/`{'ok': True}`。

### 4. store（`src/stores/aiChat.ts`）

- `paletteItems: Record<string, { commands: McpLikeItem[]; skills: McpLikeItem[] }>`（缓存）。`Item = {name, description}`。
- `loadPaletteItems(sid)`：调 `getCommands(sid)`，存缓存；失败静默置空（仿 `loadFiles`）。在 `openSession` 调用一次。
- `runCommand(sid, name, args)`：`this.streaming[sid]=true; this.thinking[sid]=true; this._resetStreamState(sid)`；push 一条本地 user 消息显示 `/name args`（便于回看，仿 `sendUserMessage`）；调 `postCommand(sid, name, args)`。输出经现有 SSE。

### 5. api（`src/api/aiChat.ts`）

```ts
export interface PaletteCommand { name: string; description: string }
export function getCommands(id: string) {
  return get<{ commands: PaletteCommand[]; skills: PaletteCommand[] }>(`/ai/chat/sessions/${encodeURIComponent(id)}/commands`)
}
export function postCommand(id: string, command: string, args: string) {
  return post<{ ok: boolean }>(`/ai/chat/sessions/${encodeURIComponent(id)}/command`, { command, arguments: args })
}
```

### 6. AiChatView 接线

- 计算 `paletteOpen`：`input` 以 `/` 开头且非空 → true。`paletteItems` = 合并 store 缓存的 commands/skills + 内置命令，按 `/` 后已输入文本 `startsWith`/`includes` 过滤。`activeIndex` 维护高亮。
- `onKey` 升级：当 `paletteOpen` 时，↑/↓ 改 `activeIndex`、Enter/Tab 接受当前项（不发送）、Esc 关闭；否则原 Enter 发送逻辑。
- 接受一项 `acceptItem(item)`：
  - `builtin`/`command` → `input.value = '/' + item.name + ' '`（聚焦末尾，等待参数）。
  - `skill` → `input.value = '使用 \`' + item.name + '\` 技能:'`（聚焦末尾）。
  - 关闭下拉。
- `send()` 升级：trim 后若以 `/` 开头：取首段 `name`（去斜杠）、其余为 `args`：
  1. `findFrontendCommand(name)` 命中 → `await cmd.run(store)`（清空输入）。
  2. 否则 `name` 命中 store 缓存的 opencode 命令 → `await store.runCommand(sid, name, args)`。
  3. 否则（未知 `/xxx`）→ 按普通 `sendUserMessage(text)`。
  - 非 `/` → 原 `sendUserMessage`。

## 数据流

1. `openSession` → `loadPaletteItems` 拉 `/commands`（命令+技能）缓存。
2. 输入 `/` → `paletteOpen` 真 → 过滤渲染 `CommandPalette`。
3. ↑↓ 选 + Enter/Tab 接受 → 命令填 `/<name> `、技能插脚手架。
4. 回车 `send()` 分流：前端命令 `run`；opencode 命令 `runCommand`→`POST /command`→SSE 回合；技能/未知/普通→`sendUserMessage`。

## 关键风险

- **命令回合是否走按目录 scope 的 SSE**：实现 Task 1 先真机冒烟：建会话→订阅 `/event?directory=ws`→`POST /session/:id/command` 跑一个无副作用命令→确认收到 `message.*`/`session.idle`。若不成立，改用其它流或回退方案，需回设计。
- **command body 的 `model` 形态**：先按 prompt 的 `{providerID,modelID}`，冒烟时核对（可能 string）。

## 错误处理

| 情况 | 行为 |
|------|------|
| `GET /command`/`/skill` 失败 | 该列表 `[]`；下拉仍显示内置命令 |
| `POST /command` 失败 | `ElMessage.error('执行失败')`，清 streaming |
| 未知 `/xxx` | 当普通消息发送 |
| 会话不归属 | 403/404（同其它路由） |

## 测试

**后端**
- `test_opencode_client.py`：`list_commands`/`list_skills` 传 `?directory=` + 解析；`run_command` POST 到 `/session/:id/command`，body `{command,arguments}` + 含 model 时形态正确。
- `test_routes_ai_chat.py`：`GET /commands` 归属校验 + 合并（mock `list_commands`/`list_skills`）；任一失败→`[]`；`POST /command` 归属校验 + guest 403（`@write_required`）+ 调 `run_command` 参数正确。

**前端（Vitest，stub Element Plus）**
- `chat-commands`：`findFrontendCommand('mcps'|'mcp'|'unknown')`。
- `CommandPalette`：分组渲染、高亮 `activeIndex`、点击 emit `select`、空列表不渲染。
- `aiChat` store：`loadPaletteItems` 缓存；`runCommand` 置 streaming + push user 消息 + 调 `postCommand`。
- `AiChatView` `send()` 解析分流：`/mcps`→前端 run；`/init x`→`runCommand('init','x')`；`/unknown`→`sendUserMessage`；普通文本→`sendUserMessage`。（可把 `send` 的解析抽成纯函数 `parseCommandLine(text)` 以便单测。）

**真机**：输入 `/` 出现下拉（内置/命令/技能）；选 `/mcps` 回车→MCP 块;选某 opencode 命令补参数回车→看到回合流式输出;选某技能→输入框出现脚手架。

## 影响文件清单

- 新增：`src/components/ai-chat/chat-commands.ts`、`src/components/ai-chat/CommandPalette.vue`
- 改：`src/api/aiChat.ts`、`src/stores/aiChat.ts`、`src/views/ai-chat/AiChatView.vue`
- 改：`server/utils/opencode_client.py`、`server/routes/ai_chat.py`
- 测试：`server/tests/test_opencode_client.py`、`server/tests/test_routes_ai_chat.py`、`src/components/ai-chat/__tests__/`（chat-commands、CommandPalette）、`src/stores/__tests__/`、AiChatView send 解析单测
