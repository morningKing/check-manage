# AI Chat — OpenCode primary agent 选择器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 AI chat 输入区加一个 per-session 的 OpenCode **primary agent** 选择器（build/plan 等），与现有 model 选择器同构。

**Architecture:** 后端新增 `GET /ai/chat/agents`（过滤掉 OpenCode 内部 primary agent），并让 `send_prompt_async` 接受 `agent` 字段透传给 OpenCode 的 `prompt_async`；前端镜像 model 选择器：store 加 `agentBySession` + set/hydrate，输入区加一个 agent 下拉。

**Tech Stack:** Flask + requests（后端，Pytest）；Vue 3 + Pinia + Element Plus（前端，Vitest）。

设计文档：`docs/superpowers/specs/2026-06-09-ai-chat-agent-picker-design.md`

---

## File Structure

修改：
- `server/utils/opencode_client.py` — 加 `list_agents()`；`send_prompt_async` 加 `agent` 形参
- `server/routes/ai_chat.py` — 加 `GET /ai/chat/agents` 路由 + 内部 agent 常量；`send_message` 读 `agent`
- `server/tests/test_routes_ai_chat.py` — 新增 agents 路由 + send-with-agent 测试
- `src/api/aiChat.ts` — 加 `listAgents()` + `AgentInfo`；`sendMessage` 加 `agent`
- `src/stores/aiChat.ts` — 加 `agentBySession` + `setSessionAgent`/`hydrateSessionAgent`；`sendUserMessage` 透传 agent
- `src/stores/__tests__/aiChat.agent.test.ts` — store 测试
- `src/views/ai-chat/AiChatView.vue` — agent 下拉

---

## Task 1: 后端 `GET /ai/chat/agents` + `list_agents`

**Files:**
- Modify: `server/utils/opencode_client.py`
- Modify: `server/routes/ai_chat.py`
- Test: `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: Write the failing test**

在 `server/tests/test_routes_ai_chat.py` 末尾追加（该文件 `setup` fixture yields `(client, dev_h, ...)` 并把 `OpenCodeClient` patch 成 `fake_client`，记作 `oc`；参考既有 `test_list_models_flattens_connected_providers`）：

```python
def test_list_agents_filters_internal_and_subagents(setup):
    client, dev_h, oc = setup[0], setup[1], setup[2]
    oc.list_agents.return_value = [
        {"name": "build", "description": "default", "mode": "primary"},
        {"name": "plan", "description": "no edits", "mode": "primary"},
        {"name": "compaction", "description": "", "mode": "primary"},
        {"name": "title", "description": "", "mode": "primary"},
        {"name": "summary", "description": "", "mode": "primary"},
        {"name": "general", "description": "subagent", "mode": "subagent"},
        {"name": "explore", "description": "subagent", "mode": "subagent"},
    ]
    resp = client.get('/ai/chat/agents', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    names = [a['name'] for a in body['agents']]
    assert names == ['build', 'plan']
    assert body['default'] == 'build'
```

> 注意：先确认 `setup` fixture 实际 yield 的元组形状（打开文件看 `yield ...` 一行）。若 `oc` / `dev_h` 的获取方式与上面不同，按该文件既有测试（如 `test_list_models_flattens_connected_providers`、`test_send_message_uses_body_model_when_provided`）的取法对齐，保持三条断言意图不变。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py::test_list_agents_filters_internal_and_subagents -v`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: Add `list_agents` to OpenCodeClient**

在 `server/utils/opencode_client.py` 的 `list_skills` 方法后追加（同形）：

```python
    def list_agents(self, directory: str = "") -> list:
        """Return OpenCode's agent list. Each item has name/description/mode
        ('primary' | 'subagent'). Not strictly directory-scoped, but accept the
        param for parity with list_skills/list_commands."""
        params = {"directory": directory} if directory else None
        resp = requests.get(self._url("/agent"), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: Add the route**

在 `server/routes/ai_chat.py` 的 `list_models` 路由附近（其后）新增。先在模块顶部（import 之后、路由之前的常量区）加：

```python
# OpenCode 内部系统 agent（mode=primary 但不应出现在用户选择器）
INTERNAL_AGENTS = {'compaction', 'title', 'summary'}
```

再加路由：

```python
@ai_chat_bp.route('/agents', methods=['GET'])
@login_required
def list_agents():
    """List user-facing primary OpenCode agents for the composer dropdown.

    Returns { "agents": [{ "name", "description" }], "default": "<name>"|null }.
    Filters to mode=='primary' and excludes OpenCode's internal agents.
    """
    try:
        raw = OpenCodeClient(OPENCODE_BASE_URL).list_agents()
    except Exception as e:
        return jsonify({'error': f'OpenCode unreachable: {e}', 'agents': [], 'default': None}), 502

    agents = [
        {'name': a.get('name'), 'description': a.get('description') or ''}
        for a in (raw or [])
        if a.get('mode') == 'primary' and a.get('name') not in INTERNAL_AGENTS
    ]
    names = {a['name'] for a in agents}
    default = 'build' if 'build' in names else (agents[0]['name'] if agents else None)
    return jsonify({'agents': agents, 'default': default})
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py::test_list_agents_filters_internal_and_subagents -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add server/utils/opencode_client.py server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): GET /ai/chat/agents lists user-facing primary agents"
```

---

## Task 2: 后端 `send_message` 透传 `agent`

**Files:**
- Modify: `server/utils/opencode_client.py`
- Modify: `server/routes/ai_chat.py:319-328`
- Test: `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: Write the failing test**

追加（镜像既有 `test_send_message_uses_body_model_when_provided`，它读 `oc.send_prompt_async.call_args`）：

```python
def test_send_message_passes_body_agent_to_opencode(setup):
    client, dev_h, oc = setup[0], setup[1], setup[2]
    # 复用既有 send_message 测试的会话准备方式（见 test_send_message_persists_user_and_calls_opencode）
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': 'hi', 'attachments': [], 'agent': 'plan'},
        headers=dev_h,
    )
    assert resp.status_code == 202
    _, kwargs = oc.send_prompt_async.call_args
    assert kwargs.get('agent') == 'plan'
    assert resp.get_json().get('agent') == 'plan'
```

> 用 `test_send_message_persists_user_and_calls_opencode` / `test_send_message_uses_body_model_when_provided` 里相同的会话/游标准备（session id、`oc` 取法、headers）。保持两条断言意图：`send_prompt_async` 收到 `agent='plan'`，响应体含 `agent: 'plan'`。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py::test_send_message_passes_body_agent_to_opencode -v`
Expected: FAIL（`send_prompt_async` 无 `agent` kwarg / 响应无 agent）

- [ ] **Step 3: Add `agent` param to `send_prompt_async`**

在 `server/utils/opencode_client.py` 修改 `send_prompt_async` 签名与 body（保留现有 model/directory 逻辑）：

```python
    def send_prompt_async(self, opencode_session_id: str, content: str,
                          model: str = "", directory: str = "", agent: str = "") -> None:
        body = {"parts": [{"type": "text", "text": content}]}
        if model and "/" in model:
            provider_id, model_id = model.split("/", 1)
            body["model"] = {"providerID": provider_id, "modelID": model_id}
        if agent:
            body["agent"] = agent
        params = {"directory": directory} if directory else None
        resp = requests.post(
            self._url(f"/session/{opencode_session_id}/prompt_async"),
            params=params,
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
```

（保留原有 docstring；仅加 `agent` 形参与 `if agent: body["agent"] = agent`。）

- [ ] **Step 4: Read `agent` in send_message route**

在 `server/routes/ai_chat.py` 把现有发送段（约 319-328 行）改为：

```python
    requested_model = (body.get('model') or '').strip()
    effective_model = requested_model or OPENCODE_MODEL
    requested_agent = (body.get('agent') or '').strip()
    OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(
        sess[2], prompt.strip(), model=effective_model, directory=sess[4],
        agent=requested_agent,
    )
    ensure_listener(sid, sess[2], sess[4])
    return jsonify({
        'messageId': msg_id,
        'model': effective_model or None,
        'agent': requested_agent or None,
    }), 202
```

- [ ] **Step 5: Run tests to verify pass (and no regression)**

Run: `cd server && set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 && python -m pytest tests/test_routes_ai_chat.py -v`
Expected: 新测试 PASS，既有 send_message/model 测试不回归

- [ ] **Step 6: Commit**

```bash
git add server/utils/opencode_client.py server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): send_message honors per-message agent override"
```

---

## Task 3: 前端 API `listAgents` + `sendMessage` 加 agent

**Files:**
- Modify: `src/api/aiChat.ts:84-105`

- [ ] **Step 1: Update `sendMessage` signature**

在 `src/api/aiChat.ts` 找到现有 `sendMessage`（约 84-90 行）：

```typescript
export function sendMessage(
  id: string, content: string, attachments: string[] = [], model = '',
) {
  return post<{ messageId: string; model: string | null }>(
    `/ai/chat/sessions/${id}/messages`,
    { content, attachments, model },
  )
}
```

改为（加 `agent` 形参与 body 字段、响应类型加 `agent`）：

```typescript
export function sendMessage(
  id: string, content: string, attachments: string[] = [], model = '', agent = '',
) {
  return post<{ messageId: string; model: string | null; agent?: string | null }>(
    `/ai/chat/sessions/${id}/messages`,
    { content, attachments, model, agent },
  )
}
```

> 保持 url 与既有写法一致；若现有签名/默认参数略有出入，按文件实际为准，只新增 `agent`。

- [ ] **Step 2: Add `listAgents` + `AgentInfo`**

在 `ModelInfo` / `listModels` 定义附近追加：

```typescript
export interface AgentInfo {
  name: string
  description: string
}

export function listAgents() {
  return get<{ agents: AgentInfo[]; default: string | null }>('/ai/chat/agents')
}
```

> 确认文件顶部已 `import { get, post } from ...`（`listModels` 用了 `get`，应已存在）。

- [ ] **Step 3: Typecheck**

Run: `npx vue-tsc --noEmit 2>&1 | tail -20`
Expected: 无新增错误（此步会因 store 仍以 4 参调用 `sendMessage` 而 OK——第 5 参有默认值）

- [ ] **Step 4: Commit**

```bash
git add src/api/aiChat.ts
git commit -m "feat(ai-chat): listAgents api + sendMessage agent param"
```

---

## Task 4: 前端 store `agentBySession` + 透传 + 持久化

**Files:**
- Modify: `src/stores/aiChat.ts`（state、`sendUserMessage`、新增 set/hydrate）
- Test: `src/stores/__tests__/aiChat.agent.test.ts`

- [ ] **Step 1: Write the failing test**

新建 `src/stores/__tests__/aiChat.agent.test.ts`：

```typescript
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  sendMessage: vi.fn().mockResolvedValue({ messageId: 'm1', model: null, agent: null }),
  // 其余被 store 引用的导出给最小 stub，避免 import 报错
  createSession: vi.fn(), getSessions: vi.fn(), getMessages: vi.fn(),
  deleteSession: vi.fn(), deleteFromMessage: vi.fn(), uploadFile: vi.fn(),
  uploadSkill: vi.fn(), renameSession: vi.fn(), runScript: vi.fn(),
  listModels: vi.fn(), listAgents: vi.fn(), getFileDiff: vi.fn(),
  downloadFileUrl: vi.fn(), abortStream: vi.fn(),
}))

import { sendMessage } from '@/api/aiChat'
import { useAiChatStore } from '@/stores/aiChat'

describe('aiChat store agent', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('setSessionAgent persists and hydrateSessionAgent restores', () => {
    const s = useAiChatStore()
    s.setSessionAgent('sid1', 'plan')
    expect(s.agentBySession['sid1']).toBe('plan')
    expect(localStorage.getItem('check-manage:ai-chat:agent:sid1')).toBe('plan')

    const s2 = useAiChatStore()
    delete s2.agentBySession['sid1']
    s2.hydrateSessionAgent('sid1')
    expect(s2.agentBySession['sid1']).toBe('plan')
  })

  it('sendUserMessage forwards the session agent to the api', async () => {
    const s = useAiChatStore()
    s.activeSessionId = 'sid1'
    s.messages['sid1'] = []
    s.agentBySession['sid1'] = 'plan'
    await s.sendUserMessage('hello')
    const call = (sendMessage as any).mock.calls.at(-1)
    // sendMessage(sid, content, paths, model, agent)
    expect(call[0]).toBe('sid1')
    expect(call[4]).toBe('plan')
  })
})
```

> 该测试可能依赖 store 内部初始化细节（如 streaming/attachments 字段）。若 `sendUserMessage` 因缺少某些会话态而抛错，按 store 实际所需补齐对应 `s.xxx['sid1'] = ...` 初始值，保持两条断言意图：持久化往返、`sendMessage` 第 5 参为所选 agent。

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/stores/__tests__/aiChat.agent.test.ts`
Expected: FAIL（`setSessionAgent` / `agentBySession` 未定义）

- [ ] **Step 3: Add state `agentBySession`**

在 `src/stores/aiChat.ts` 的 state 里、`modelBySession: {},` 之后加：

```typescript
      agentBySession: {} as Record<string, string>,
```

并在 state 的类型/接口处（`modelBySession: Record<string, string>` 声明附近）加：

```typescript
  agentBySession: Record<string, string>
```

- [ ] **Step 4: Forward agent in `sendUserMessage`**

在 `sendUserMessage` 里，找到：

```typescript
      const model = this.modelBySession[sid] || ''
      const { messageId } = await sendMessage(sid, content, paths, model)
```

改为：

```typescript
      const model = this.modelBySession[sid] || ''
      const agent = this.agentBySession[sid] || ''
      const { messageId } = await sendMessage(sid, content, paths, model, agent)
```

- [ ] **Step 5: Add `setSessionAgent` / `hydrateSessionAgent`**

在 `setSessionModel` / `hydrateSessionModel` 之后追加（镜像实现）：

```typescript
    /** Update the composer's selected agent for a session; persist to localStorage. */
    setSessionAgent(sessionId: string, agent: string) {
      this.agentBySession[sessionId] = agent
      try {
        const key = `check-manage:ai-chat:agent:${sessionId}`
        if (agent) localStorage.setItem(key, agent)
        else localStorage.removeItem(key)
      } catch { /* private mode etc. */ }
    },

    /** Hydrate `agentBySession[id]` from localStorage when a session is opened. */
    hydrateSessionAgent(sessionId: string) {
      if (this.agentBySession[sessionId] !== undefined) return
      try {
        const stored = localStorage.getItem(`check-manage:ai-chat:agent:${sessionId}`)
        if (stored) this.agentBySession[sessionId] = stored
      } catch { /* ignore */ }
    },
```

- [ ] **Step 6: Run test to verify it passes**

Run: `npx vitest run src/stores/__tests__/aiChat.agent.test.ts`
Expected: PASS（2 passed）

- [ ] **Step 7: Commit**

```bash
git add src/stores/aiChat.ts src/stores/__tests__/aiChat.agent.test.ts
git commit -m "feat(ai-chat): per-session agentBySession state + persistence + forward to api"
```

---

## Task 5: 前端 AiChatView agent 下拉

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue`（script:33,42-67,253-265；template:679-697）

- [ ] **Step 1: Import `listAgents` / `AgentInfo`**

把第 33 行的 import 补上 `listAgents` 和 `AgentInfo`：

```typescript
import { downloadFileUrl, runScript, listModels, listAgents, getFileDiff, type AiMessage, type ChangedFile, type ModelInfo, type AgentInfo, type FileDiff } from '@/api/aiChat'
```

- [ ] **Step 2: Add agent state + fetch + computed (mirror model)**

在 `composerModel` computed（约 56-59 行）之后追加：

```typescript
// Composer agent picker: list from OpenCode's /agent via backend; selection is
// per-session and persisted via the store's setSessionAgent / hydrateSessionAgent.
const agents = ref<AgentInfo[]>([])
const agentsLoading = ref(false)
async function fetchAgents() {
  if (agentsLoading.value) return
  agentsLoading.value = true
  try {
    const r = await listAgents()
    agents.value = r.agents
  } catch { /* surfaced by interceptor */ }
  finally { agentsLoading.value = false }
}
const composerAgent = computed<string>({
  get: () => (activeId.value ? store.agentBySession[activeId.value] || '' : ''),
  set: (v) => { if (activeId.value) store.setSessionAgent(activeId.value, v) },
})
```

- [ ] **Step 3: Hydrate agent wherever model hydrates**

在每个 `store.hydrateSessionModel(...)` 调用处（`openSession` 约 65 行、onMounted 约 253 与 265 行）紧随其后加同参数的 `store.hydrateSessionAgent(...)`。例如 `openSession`：

```typescript
    store.hydrateSessionModel(sessionId)
    store.hydrateSessionAgent(sessionId)
```

并在 onMounted 里 `fetchModels()` 的预取附近加一行 `fetchAgents()`（best-effort，约 256 行注释“pre-fetch models”处）。

- [ ] **Step 4: Add the agent `<ElSelect>` next to the model one**

在 template 的 `composer-bar__right` 里、model `<ElSelect>`（约 680-697 行）之前插入：

```vue
                <ElSelect
                  v-if="activeId"
                  v-model="composerAgent"
                  class="composer-agent"
                  size="small"
                  placeholder="默认 Agent"
                  clearable
                  :loading="agentsLoading"
                  @visible-change="(v) => v && !agents.length && fetchAgents()"
                >
                  <ElOption value="" label="默认 Agent" />
                  <ElOption
                    v-for="a in agents" :key="a.name"
                    :value="a.name" :label="a.name"
                  />
                </ElSelect>
```

- [ ] **Step 5: Add a style rule for `.composer-agent`**

在 `.composer-model { ... }`（约 953 行）附近追加：

```scss
.composer-agent { width: 120px; margin-right: 6px; }
```

- [ ] **Step 6: Typecheck**

Run: `npx vue-tsc --noEmit 2>&1 | tail -20`
Expected: 无新增错误

- [ ] **Step 7: Manual smoke (record in commit message)**

启动 `npm run dev:all`（需 OpenCode 在 127.0.0.1:4096 运行），打开 AI 助手 → 选会话 → 输入区出现「默认 Agent」下拉，可选 build/plan；选 plan 后发一条消息，确认请求体含 `agent: "plan"`（Network 面板）且回复按 plan 行为（不改文件）。刷新后该会话仍记住所选 agent。

- [ ] **Step 8: Commit**

```bash
git add src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): composer agent dropdown (per-session, persisted)"
```

---

## Self-Review 结果

- **Spec 覆盖**：`list_agents`(T1) / `/ai/chat/agents` 过滤+default(T1) / `send_prompt_async` agent(T2) / `send_message` 读 agent(T2) / 前端 `listAgents`+`sendMessage`(T3) / `agentBySession`+set+hydrate+透传(T4) / AiChatView 下拉(T5) / 测试(T1,T2,T4) 均有任务。✓
- **占位符**：无 TBD/TODO；每个改动给出完整代码。对依赖既有测试 fixture 形状/ store 内部初始化的两处，明确要求“按文件实际对齐、保持断言意图”，非占位。✓
- **类型一致**：`sendMessage(sid, content, paths, model, agent)` 五参签名在 T3 定义、T4 调用、test 断言 `call[4]` 一致；`AgentInfo {name,description}`、`listAgents -> {agents, default}` 在 T3/T1 一致；store `agentBySession` / `setSessionAgent` / `hydrateSessionAgent` 命名贯穿 T4/T5。✓
