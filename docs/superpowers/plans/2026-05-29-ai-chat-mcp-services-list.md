# AI 助手「MCP 服务」列表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AI 助手输入 `/mcps`（或 `/mcp`）时，确定性地（不经过模型）渲染本系统已配置的 MCP 服务及其工具清单。

**Architecture:** 前端 `send()` 拦截 `/mcps` → 调 Flask `GET /ai/chat/sessions/:id/mcp` → 该路由合并两个来源（OpenCode `GET /mcp?directory=` 给出服务名+连接状态；我方 MCP server 新增的 `GET /tools` 给出工具名+描述）→ 前端把结果作为一条本地临时消息（part 类型 `mcp_services`）插入对话流，用新组件 `McpServicesBlock.vue` 渲染。

**Tech Stack:** MCP server（FastAPI，独立 .venv）、Flask（psycopg2）、Vue 3 + TS + Element Plus + Pinia。

参考 spec：`docs/superpowers/specs/2026-05-29-ai-chat-mcp-services-list-design.md`

**约定（Windows）**
- mcp-server 测试：`cd mcp-server && ./.venv/Scripts/python.exe -m pytest <file> -v`
- server 测试：`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest <file> -v`（`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 必需）
- 前端测试：`npx vitest run <file>`

---

## File Structure

- `mcp-server/tools/__init__.py` — 新增 `tool_specs()`：返回 `[(name, description)]`。
- `mcp-server/main.py` — 新增 `GET /tools` 路由，调用 `tool_specs()`。
- `server/utils/opencode_client.py` — 新增 `list_mcp(directory)`。
- `server/routes/ai_chat.py` — 新增 `GET /sessions/:id/mcp`（+ 顶部 `import requests`）。
- `src/api/aiChat.ts` — 类型 `McpTool`/`McpServer`、`AiContentPart` 加 `mcp_services`、`getMcpServices()`。
- `src/stores/aiChat.ts` — `showMcpServices()` action。
- `src/components/ai-chat/McpServicesBlock.vue` — 新组件。
- `src/views/ai-chat/AiChatView.vue` — `send()` 拦截 + 渲染分支 + 组件 import。
- 测试：`mcp-server/tests/test_tools_endpoint.py`(新)、`server/tests/test_opencode_client.py`、`server/tests/test_routes_ai_chat.py`、`src/stores/__tests__/aiChat.mcp.test.ts`(新)、`src/components/ai-chat/__tests__/McpServicesBlock.test.ts`(新)。

---

### Task 1: MCP server `/tools` 端点

**Files:**
- Modify: `mcp-server/tools/__init__.py`
- Modify: `mcp-server/main.py`
- Test: `mcp-server/tests/test_tools_endpoint.py` (create)

- [ ] **Step 1: 写失败测试**

Create `mcp-server/tests/test_tools_endpoint.py`:

```python
"""GET /tools exposes the MCP tool registry (name + description) — no auth,
no business data. Used by the chat's MCP 服务 block."""

from fastapi.testclient import TestClient


def test_tool_specs_lists_registered_tools():
    from tools import tool_specs
    names = {n for n, _ in tool_specs()}
    assert {"list_collections", "query_collection", "run_python"} <= names
    # every entry carries a (possibly empty) string description
    assert all(isinstance(d, str) for _, d in tool_specs())


def test_tools_endpoint_returns_name_and_description():
    from main import app
    with TestClient(app) as c:
        resp = c.get("/tools")
    assert resp.status_code == 200
    body = resp.json()
    by_name = {t["name"]: t for t in body}
    assert "list_collections" in by_name
    assert by_name["list_collections"]["description"]  # non-empty
```

- [ ] **Step 2: 运行确认失败**

Run: `cd mcp-server && ./.venv/Scripts/python.exe -m pytest tests/test_tools_endpoint.py -v`
Expected: FAIL (`cannot import name 'tool_specs'`; 404 on `/tools`).

- [ ] **Step 3: 实现 `tool_specs()`**

In `mcp-server/tools/__init__.py`, after the `_TOOLS = {...}` dict, add:

```python
def tool_specs() -> list[tuple[str, str]]:
    """(name, description) for every registered tool — used by GET /tools."""
    return [(spec.name, spec.description or "") for spec, _ in _TOOLS.values()]
```

- [ ] **Step 4: 实现 `/tools` 路由**

In `mcp-server/main.py`, change the tools import and add a route next to `/health`:

```python
# was: from tools import register_all
from tools import register_all, tool_specs
```

```python
@app.get("/tools")
def tools():
    return [{"name": n, "description": d} for n, d in tool_specs()]
```

(`/tools` does not start with `/mcp`, so `TokenMiddleware` skips it — no token needed, same as `/health`.)

- [ ] **Step 5: 运行确认通过**

Run: `cd mcp-server && ./.venv/Scripts/python.exe -m pytest tests/test_tools_endpoint.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: 全量 MCP 测试无回归**

Run: `cd mcp-server && ./.venv/Scripts/python.exe -m pytest -q`
Expected: previously-passing count + 2, 8 skipped.

- [ ] **Step 7: Commit**

```bash
git add mcp-server/tools/__init__.py mcp-server/main.py mcp-server/tests/test_tools_endpoint.py
git commit -m "feat(mcp): GET /tools exposes tool registry (name + description)"
```

---

### Task 2: OpenCodeClient `list_mcp`

**Files:**
- Modify: `server/utils/opencode_client.py`
- Test: `server/tests/test_opencode_client.py`

- [ ] **Step 1: 写失败测试**

Append to `server/tests/test_opencode_client.py`:

```python
def test_list_mcp_scopes_by_directory_and_returns_servers():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"check-manage": {"status": "connected"}}
    fake_resp.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.get", return_value=fake_resp) as get:
        from utils.opencode_client import OpenCodeClient
        out = OpenCodeClient("http://127.0.0.1:4096").list_mcp("/ws")
    assert out == {"check-manage": {"status": "connected"}}
    args, kwargs = get.call_args
    assert args[0].endswith("/mcp")
    assert kwargs["params"] == {"directory": "/ws"}


def test_list_mcp_omits_directory_when_empty():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {}
    fake_resp.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.get", return_value=fake_resp) as get:
        from utils.opencode_client import OpenCodeClient
        OpenCodeClient("http://127.0.0.1:4096").list_mcp()
    assert get.call_args.kwargs.get("params") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_opencode_client.py -v`
Expected: FAIL (`OpenCodeClient` has no attribute `list_mcp`).

- [ ] **Step 3: 实现 `list_mcp`**

In `server/utils/opencode_client.py`, add a method to `OpenCodeClient` (e.g. after `subscribe_events`):

```python
def list_mcp(self, directory: str = "") -> dict:
    """Return configured MCP servers + connection status for `directory`, e.g.
    {"check-manage": {"status": "connected"}}. The un-scoped /mcp returns {}.
    """
    params = {"directory": directory} if directory else None
    resp = requests.get(self._url("/mcp"), params=params, timeout=self.timeout)
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 4: 运行确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_opencode_client.py -v`
Expected: PASS (all, including 2 new).

- [ ] **Step 5: Commit**

```bash
git add server/utils/opencode_client.py server/tests/test_opencode_client.py
git commit -m "feat(opencode): list_mcp() returns configured MCP servers + status"
```

---

### Task 3: Flask 路由 `GET /sessions/:id/mcp`

**Files:**
- Modify: `server/routes/ai_chat.py`
- Test: `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: 写失败测试**

Append to `server/tests/test_routes_ai_chat.py` (uses the existing `setup` fixture which yields `client, cursor, fake_client(oc), dev_h, guest_h, tmp_path`; `cursor.fetchone` drives `_load_session_for_user`):

```python
def test_list_mcp_services_merges_servers_and_tools(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    oc.list_mcp.return_value = {'check-manage': {'status': 'connected'}}
    tools_resp = MagicMock()
    tools_resp.json.return_value = [{'name': 'list_collections', 'description': 'List collections.'}]
    with patch('routes.ai_chat.requests.get', return_value=tools_resp):
        resp = client.get('/ai/chat/sessions/sess_x/mcp', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['servers'] == [{
        'name': 'check-manage',
        'status': 'connected',
        'tools': [{'name': 'list_collections', 'description': 'List collections.'}],
    }]
    assert oc.list_mcp.call_args[0][0] == '/tmp/ws'  # scoped to the workspace


def test_list_mcp_services_other_users_session_404(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = None
    resp = client.get('/ai/chat/sessions/sess_other/mcp', headers=dev_h)
    assert resp.status_code == 404


def test_list_mcp_services_opencode_down_returns_empty(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    oc.list_mcp.side_effect = Exception('boom')
    resp = client.get('/ai/chat/sessions/sess_x/mcp', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['servers'] == []
    assert body['error'] == 'opencode unavailable'
```

- [ ] **Step 2: 运行确认失败**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_routes_ai_chat.py -k mcp_services -v`
Expected: FAIL (404 for the success test — route not found; or `requests` not importable in `routes.ai_chat`).

- [ ] **Step 3: 加 `import requests`**

In `server/routes/ai_chat.py`, add to the top imports (after `import secrets`):

```python
import requests
```

- [ ] **Step 4: 实现路由**

In `server/routes/ai_chat.py`, add (near the `list_changes` route):

```python
@ai_chat_bp.route('/sessions/<sid>/mcp', methods=['GET'])
@login_required
def list_mcp_services(sid):
    """List configured MCP servers + their tools for the chat's MCP 服务 block.
    Deterministic: servers/status from OpenCode, tools from our MCP server — never
    via the model."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    try:
        servers_raw = OpenCodeClient(OPENCODE_BASE_URL).list_mcp(sess[4])
    except Exception:
        return jsonify({'servers': [], 'error': 'opencode unavailable'})
    try:
        our_tools = requests.get(f"{MCP_SERVER_URL}/tools", timeout=5).json()
    except Exception:
        our_tools = []
    servers = [
        {
            'name': name,
            'status': (servers_raw.get(name) or {}).get('status', 'unknown'),
            'tools': our_tools if name == MCP_NAME else [],
        }
        for name in sorted(servers_raw.keys())
    ]
    return jsonify({'servers': servers})
```

- [ ] **Step 5: 运行确认通过**

Run: `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_routes_ai_chat.py -k mcp_services -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): GET /sessions/:id/mcp merges MCP servers + tools"
```

---

### Task 4: 前端 API 层

**Files:**
- Modify: `src/api/aiChat.ts`

(No standalone test — covered by the store/component tasks. Type-only + thin shim.)

- [ ] **Step 1: 加类型 + part + 函数**

In `src/api/aiChat.ts`, add the interfaces (after `ChangedFile`):

```ts
export interface McpTool { name: string; description: string }
export interface McpServer { name: string; status: string; tools: McpTool[] }
```

Extend the `AiContentPart` union with a new member:

```ts
  | { type: 'mcp_services'; servers: McpServer[] }
```

Add the API function (near `getChanges`):

```ts
export function getMcpServices(id: string) {
  return get<{ servers: McpServer[]; error?: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/mcp`,
  )
}
```

- [ ] **Step 2: 类型检查通过**

Run: `npx vue-tsc --noEmit -p tsconfig.json`
Expected: no new errors from `aiChat.ts`. (If the repo has no standalone tsc script, this step is satisfied by the build in Task 6.)

- [ ] **Step 3: Commit**

```bash
git add src/api/aiChat.ts
git commit -m "feat(ai-chat): api types + getMcpServices + mcp_services part"
```

---

### Task 5: Store `showMcpServices`

**Files:**
- Modify: `src/stores/aiChat.ts`
- Test: `src/stores/__tests__/aiChat.mcp.test.ts` (create)

- [ ] **Step 1: 写失败测试**

Create `src/stores/__tests__/aiChat.mcp.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  getMcpServices: vi.fn(),
  // other named exports used by the store module must exist as no-ops
  createSession: vi.fn(), listSessions: vi.fn(), renameSession: vi.fn(),
  deleteSession: vi.fn(), getMessages: vi.fn(), sendMessage: vi.fn(),
  uploadFile: vi.fn(), listFiles: vi.fn(), getChanges: vi.fn(),
  createEventStream: vi.fn(() => ({ close() {} })),
}))

import { getMcpServices } from '@/api/aiChat'
import { useAiChatStore } from '@/stores/aiChat'

beforeEach(() => setActivePinia(createPinia()))

describe('showMcpServices', () => {
  it('pushes an mcp_services part with the fetched servers', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.messages['s1'] = []
    ;(getMcpServices as any).mockResolvedValue({
      servers: [{ name: 'check-manage', status: 'connected', tools: [{ name: 'list_collections', description: 'x' }] }],
    })
    await store.showMcpServices()
    const last = store.messages['s1'].at(-1)!
    expect(last.role).toBe('assistant')
    expect(last.content[0]).toMatchObject({ type: 'mcp_services' })
    expect((last.content[0] as any).servers[0].name).toBe('check-manage')
  })

  it('pushes empty servers when the API errors or reports error', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'
    store.messages['s1'] = []
    ;(getMcpServices as any).mockResolvedValue({ servers: [], error: 'opencode unavailable' })
    await store.showMcpServices()
    expect((store.messages['s1'].at(-1)!.content[0] as any).servers).toEqual([])
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/stores/__tests__/aiChat.mcp.test.ts`
Expected: FAIL (`showMcpServices` is not a function).

- [ ] **Step 3: 实现 action**

In `src/stores/aiChat.ts`, extend the import from `@/api/aiChat` to include `getMcpServices` and `type McpServer`:

```ts
import {
  createSession, listSessions, renameSession as apiRenameSession, deleteSession,
  getMessages, sendMessage, uploadFile, listFiles, getChanges, getMcpServices,
  createEventStream,
  type AiMessage, type AiContentPart, type AiFile, type ChangedFile, type McpServer,
} from '@/api/aiChat'
```

Add the action inside `actions: { ... }` (e.g. after `loadChanges`):

```ts
async showMcpServices() {
  const sid = this.activeSessionId
  if (!sid) return
  let servers: McpServer[] = []
  try {
    const res = await getMcpServices(sid)
    servers = res.error ? [] : res.servers
  } catch { /* leave empty; the block renders 无法获取 */ }
  ;(this.messages[sid] ?? (this.messages[sid] = [])).push({
    id: 'mcp_' + Date.now(),
    role: 'assistant',
    content: [{ type: 'mcp_services', servers }],
  })
},
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/stores/__tests__/aiChat.mcp.test.ts`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/stores/aiChat.ts src/stores/__tests__/aiChat.mcp.test.ts
git commit -m "feat(ai-chat): store showMcpServices pushes mcp_services part"
```

---

### Task 6: `McpServicesBlock.vue` + AiChatView 接线

**Files:**
- Create: `src/components/ai-chat/McpServicesBlock.vue`
- Modify: `src/views/ai-chat/AiChatView.vue`
- Test: `src/components/ai-chat/__tests__/McpServicesBlock.test.ts` (create)

- [ ] **Step 1: 写失败测试**

Create `src/components/ai-chat/__tests__/McpServicesBlock.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import McpServicesBlock from '@/components/ai-chat/McpServicesBlock.vue'

describe('McpServicesBlock', () => {
  it('renders servers, status and tools', () => {
    const w = mount(McpServicesBlock, {
      props: {
        servers: [
          { name: 'check-manage', status: 'connected', tools: [
            { name: 'list_collections', description: '列出集合' },
            { name: 'run_python', description: '' },
          ] },
        ],
      },
    })
    expect(w.text()).toContain('MCP 服务 (1)')
    expect(w.text()).toContain('check-manage')
    expect(w.text()).toContain('connected')
    expect(w.text()).toContain('list_collections')
    expect(w.text()).toContain('列出集合')
  })

  it('shows a fallback when there are no servers', () => {
    const w = mount(McpServicesBlock, { props: { servers: [] } })
    expect(w.text()).toContain('无法获取')
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/ai-chat/__tests__/McpServicesBlock.test.ts`
Expected: FAIL (cannot resolve `McpServicesBlock.vue`).

- [ ] **Step 3: 实现组件**

Create `src/components/ai-chat/McpServicesBlock.vue`:

```vue
<script setup lang="ts">
import type { McpServer } from '@/api/aiChat'

defineProps<{ servers: McpServer[] }>()
</script>

<template>
  <div class="mcp-services">
    <template v-if="servers.length">
      <div class="mcp-services__title">MCP 服务 ({{ servers.length }})</div>
      <div v-for="s in servers" :key="s.name" class="mcp-server">
        <div class="mcp-server__head">
          <span class="mcp-dot" :class="{ on: s.status === 'connected' }" />
          <span class="mcp-server__name">{{ s.name }}</span>
          <span class="mcp-server__status">{{ s.status }}</span>
        </div>
        <ul class="mcp-tools">
          <li v-for="t in s.tools" :key="t.name">
            <code>{{ t.name }}</code><span v-if="t.description"> — {{ t.description }}</span>
          </li>
          <li v-if="!s.tools.length" class="mcp-tools__empty">（无可用工具信息）</li>
        </ul>
      </div>
    </template>
    <div v-else class="mcp-services__empty">无法获取 MCP 服务（OpenCode 不可用）</div>
  </div>
</template>

<style scoped lang="scss">
.mcp-services {
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--el-fill-color-light);
  font-size: 13px;
}
.mcp-services__title { font-weight: 600; margin-bottom: 8px; }
.mcp-server { margin-bottom: 8px; }
.mcp-server__head { display: flex; align-items: center; gap: 6px; }
.mcp-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--el-color-info);
}
.mcp-dot.on { background: var(--el-color-success); }
.mcp-server__name { font-weight: 600; }
.mcp-server__status { color: var(--el-text-color-secondary); font-size: 12px; }
.mcp-tools { margin: 4px 0 0; padding-left: 18px; }
.mcp-tools li { line-height: 1.7; }
.mcp-tools code { font-family: var(--el-font-family-mono, monospace); }
.mcp-tools__empty { color: var(--el-text-color-secondary); list-style: none; }
</style>
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/components/ai-chat/__tests__/McpServicesBlock.test.ts`
Expected: PASS (2 passed).

- [ ] **Step 5: 接线 AiChatView — import 组件**

In `src/views/ai-chat/AiChatView.vue` `<script setup>`, add next to the other ai-chat component imports (e.g. after `import ToolCallBubble ...`):

```ts
import McpServicesBlock from '@/components/ai-chat/McpServicesBlock.vue'
```

- [ ] **Step 6: 接线 — `send()` 拦截 `/mcps`**

In `src/views/ai-chat/AiChatView.vue`, replace the `send()` function with:

```ts
async function send() {
  if (!canSend.value) return
  const text = input.value.trim()
  const cmd = text.toLowerCase()
  input.value = ''
  if (!activeId.value) await newSession()
  if (cmd === '/mcps' || cmd === '/mcp') {
    await store.showMcpServices()
    return
  }
  try { await store.sendUserMessage(text) } catch { ElMessage.error('发送失败') }
}
```

- [ ] **Step 7: 接线 — 渲染分支**

In `src/views/ai-chat/AiChatView.vue` template, inside the `<template #content>` part loop, add a branch next to the `RunResultBlock` one:

```html
<McpServicesBlock
  v-else-if="p.type === 'mcp_services'"
  :servers="p.servers"
/>
```

- [ ] **Step 8: 构建 + 前端全量测试无回归**

Run: `npm run build`
Expected: vue-tsc passes (the `mcp_services` part narrows to `{ servers }`), vite build succeeds.

Run: `npm test`
Expected: previously-passing count + new tests, all green.

- [ ] **Step 9: Commit**

```bash
git add src/components/ai-chat/McpServicesBlock.vue \
        src/components/ai-chat/__tests__/McpServicesBlock.test.ts \
        src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): /mcps renders MCP services block (deterministic)"
```

---

## 真机验证（全部任务完成后）

1. 重启 MCP server（让 `/tools` 生效）+ 重新构建前端 / 起 proxy。
2. 打开 AI 助手，输入 `/mcps`，回车。
3. 期望出现「MCP 服务 (1)」块：`check-manage` connected + 6 个工具（`list_collections`、`query_collection`、`export_collection_excel`、`run_python`、`read_upload`、`save_artifact`），每个带描述。
4. 边界：停掉 OpenCode 再 `/mcps` → 块显示「无法获取 MCP 服务」。

## Self-Review 备注

- **Spec 覆盖**：`/tools`(T1)、`list_mcp`(T2)、合并路由+容错(T3)、api 类型/part/函数(T4)、store action(T5)、组件+拦截+渲染(T6) —— 覆盖 spec 全部条目（含错误处理表与测试清单）。
- **类型一致**：`McpServer = {name,status,tools:McpTool[]}`、`McpTool={name,description}`、part `{type:'mcp_services';servers:McpServer[]}` 在 api/store/组件三处一致；后端返回结构与之对应。
- **触发词**：仅 `/mcps`、`/mcp`（小写比较），不建通用命令框架（YAGNI）。
