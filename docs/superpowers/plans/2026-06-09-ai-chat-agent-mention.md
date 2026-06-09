# AI Chat — `@` 子智能体 mention 补全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** aichat 输入框输入 `@` 时补全 subagent（general/explore…），选中插入 `@name`，发送时作为 OpenCode `AgentPart` 点名委派子智能体。

**Architecture:** 后端 `/ai/chat/agents` 增 `subagents` 字段、`send_prompt_async` 支持 `agent_parts` 构造 `AgentPartInput`、`send_message` 读 `agentMentions`；前端两个纯函数（`@`-token 检测 + mention 解析）+ 复用 palette（加 `prefix`/`agent` kind）+ 光标追踪 + 发送时解析透传。

**Tech Stack:** Flask + requests（后端，Pytest）；Vue 3 + Pinia + Element Plus（前端，Vitest）。

设计文档：`docs/superpowers/specs/2026-06-09-ai-chat-agent-mention-design.md`

---

## File Structure

修改：
- `server/routes/ai_chat.py` — `/ai/chat/agents` 加 `subagents`；`send_message` 读 `agentMentions`
- `server/utils/opencode_client.py` — `send_prompt_async` 加 `agent_parts`
- `server/tests/test_routes_ai_chat.py` / `server/tests/test_opencode_client.py` — 测试
- `src/api/aiChat.ts` — `AgentMention` 类型、`listAgents` 加 `subagents`、`sendMessage` 加 `agentMentions`
- `src/stores/aiChat.ts` — `subagents` 缓存 + `sendUserMessage` 解析透传
- `src/views/ai-chat/AiChatView.vue` — 光标追踪 + mention palette + acceptItem 分流
- `src/components/ai-chat/CommandPalette.vue` — `prefix` prop + `agent` kind

新增：
- `src/utils/agentMentions.ts` + `src/utils/__tests__/agentMentions.test.ts`

---

## Task 1: 后端 `/ai/chat/agents` 增加 `subagents`

**Files:**
- Modify: `server/routes/ai_chat.py:196-203`
- Test: `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: Write the failing test**

追加（`setup` fixture yields `(client, cursor, oc, dev_h, guest_h, tmp_path)`；参考既有 `test_list_agents_filters_internal_and_subagents`）：

```python
def test_list_agents_returns_subagents_separately(setup):
    client, cursor, oc, dev_h, _, _ = setup
    oc.list_agents.return_value = [
        {"name": "build", "description": "default", "mode": "primary"},
        {"name": "plan", "description": "no edits", "mode": "primary"},
        {"name": "compaction", "description": "", "mode": "primary"},
        {"name": "general", "description": "general subagent", "mode": "subagent"},
        {"name": "explore", "description": "explore subagent", "mode": "subagent"},
    ]
    resp = client.get('/ai/chat/agents', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert [a['name'] for a in body['agents']] == ['build', 'plan']          # primary 不变
    assert [a['name'] for a in body['subagents']] == ['general', 'explore']  # 新增
    assert body['default'] == 'build'
```

- [ ] **Step 2: Run, verify FAIL**

`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_routes_ai_chat.py::test_list_agents_returns_subagents_separately -v` → FAIL（无 `subagents` 键）

- [ ] **Step 3: Add `subagents` to the route**

把 `server/routes/ai_chat.py` 的 `list_agents` 路由（当前 196-203 行）改为：

```python
    agents = [
        {'name': a.get('name'), 'description': a.get('description') or ''}
        for a in (raw or [])
        if a.get('mode') == 'primary' and a.get('name') not in INTERNAL_AGENTS
    ]
    subagents = [
        {'name': a.get('name'), 'description': a.get('description') or ''}
        for a in (raw or [])
        if a.get('mode') == 'subagent' and a.get('name') not in INTERNAL_AGENTS
    ]
    names = {a['name'] for a in agents}
    default = 'build' if 'build' in names else (agents[0]['name'] if agents else None)
    return jsonify({'agents': agents, 'subagents': subagents, 'default': default})
```

并把 502 分支也补上 `subagents`（保持响应形状一致）：将
`return jsonify({'error': f'OpenCode unreachable: {e}', 'agents': [], 'default': None}), 502`
改为
`return jsonify({'error': f'OpenCode unreachable: {e}', 'agents': [], 'subagents': [], 'default': None}), 502`

- [ ] **Step 4: Run, verify PASS + no regression**

`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_routes_ai_chat.py -q` → all pass（既有 agents 测试不回归）

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): /ai/chat/agents also returns subagents"
```

---

## Task 2: 后端 `send_prompt_async` 支持 `agent_parts` + `send_message` 读 `agentMentions`

**Files:**
- Modify: `server/utils/opencode_client.py:52-78`
- Modify: `server/routes/ai_chat.py:351-363`
- Test: `server/tests/test_opencode_client.py`, `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: Write the failing client test**

追加到 `server/tests/test_opencode_client.py`（镜像 `test_send_prompt_async_includes_model_when_given` 的 mock 风格）：

```python
def test_send_prompt_async_appends_agent_parts():
    fake_resp = MagicMock(); fake_resp.raise_for_status.return_value = None
    with patch("utils.opencode_client.requests.post", return_value=fake_resp) as post:
        from utils.opencode_client import OpenCodeClient
        OpenCodeClient("http://127.0.0.1:4096").send_prompt_async(
            "ses_42", "ask @general please",
            agent_parts=[{"name": "general", "source": {"value": "@general", "start": 4, "end": 12}}],
        )
        body = post.call_args.kwargs["json"]
        assert body["parts"][0] == {"type": "text", "text": "ask @general please"}
        assert {"type": "agent", "name": "general",
                "source": {"value": "@general", "start": 4, "end": 12}} in body["parts"]
```

- [ ] **Step 2: Run, verify FAIL**

`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_opencode_client.py::test_send_prompt_async_appends_agent_parts -v` → FAIL（`agent_parts` 未知参数 / parts 无 agent 项）

- [ ] **Step 3: Add `agent_parts` to `send_prompt_async`**

把 `server/utils/opencode_client.py` 的 `send_prompt_async`（52-71 行的签名+body 构造）改为：

```python
    def send_prompt_async(self, opencode_session_id: str, content: str,
                          model: str = "", directory: str = "", agent: str = "",
                          agent_parts=None) -> None:
        """Send a prompt. `model`/`directory`/`agent` as before.

        `agent_parts` (optional): list of {name, source?} dicts; each becomes an
        OpenCode AgentPartInput {type:'agent', name, source?} appended after the
        text part, so an `@name` mention delegates to that subagent for this turn.
        """
        parts = [{"type": "text", "text": content}]
        for m in (agent_parts or []):
            name = (m.get("name") or "").strip()
            if not name:
                continue
            part = {"type": "agent", "name": name}
            src = m.get("source")
            if isinstance(src, dict) and all(k in src for k in ("value", "start", "end")):
                part["source"] = {"value": src["value"], "start": src["start"], "end": src["end"]}
            parts.append(part)
        body = {"parts": parts}
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

（保留原 docstring 主体，只新增 `agent_parts` 段落；保留 model/agent/directory/timeout 逻辑。）

- [ ] **Step 4: Run client test, verify PASS**

`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_opencode_client.py -q` → all pass

- [ ] **Step 5: Write the failing route test**

追加到 `server/tests/test_routes_ai_chat.py`：

```python
def test_send_message_passes_agent_mentions(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess_42', 'active', '/tmp/ws')
    mentions = [{"name": "general", "value": "@general", "start": 4, "end": 12}]
    resp = client.post(
        '/ai/chat/sessions/sess_x/messages',
        json={'content': 'ask @general', 'attachments': [], 'agentMentions': mentions},
        headers=dev_h,
    )
    assert resp.status_code == 202
    _, kwargs = oc.send_prompt_async.call_args
    assert kwargs.get('agent_parts') == mentions
```

- [ ] **Step 6: Run, verify FAIL**

`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_routes_ai_chat.py::test_send_message_passes_agent_mentions -v` → FAIL（无 `agent_parts` kwarg）

- [ ] **Step 7: Read `agentMentions` in the route**

把 `server/routes/ai_chat.py` 的发送段（351-357 行）改为：

```python
    requested_model = (body.get('model') or '').strip()
    effective_model = requested_model or OPENCODE_MODEL
    requested_agent = (body.get('agent') or '').strip()
    agent_mentions = body.get('agentMentions')
    if not isinstance(agent_mentions, list):
        agent_mentions = []
    OpenCodeClient(OPENCODE_BASE_URL).send_prompt_async(
        sess[2], prompt.strip(), model=effective_model, directory=sess[4],
        agent=requested_agent, agent_parts=agent_mentions,
    )
```

- [ ] **Step 8: Run all backend tests, verify PASS**

`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_routes_ai_chat.py tests/test_opencode_client.py -q` → all pass

- [ ] **Step 9: Commit**

```bash
git add server/utils/opencode_client.py server/routes/ai_chat.py server/tests/test_opencode_client.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): send_prompt_async builds AgentPart inputs from @ mentions"
```

---

## Task 3: 前端 API — `AgentMention` 类型 + `listAgents` subagents + `sendMessage` agentMentions

**Files:**
- Modify: `src/api/aiChat.ts`

- [ ] **Step 1: Add `AgentMention` + extend `sendMessage`**

在 `src/api/aiChat.ts` 找到现有 `sendMessage`（签名 `(id, content, attachments=[], model='', agent='')`，body `{content, attachments, model, agent}`），改为加 `agentMentions`：

```typescript
export interface AgentMention { name: string; value: string; start: number; end: number }

export function sendMessage(
  id: string, content: string, attachments: string[] = [], model = '', agent = '',
  agentMentions: AgentMention[] = [],
) {
  return post<{ messageId: string; model: string | null; agent?: string | null }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/messages`,
    { content, attachments, model, agent, agentMentions },
  )
}
```

（保留现有 `encodeURIComponent`/响应类型；只加第 6 参 + body 字段 + `AgentMention` 接口。）

- [ ] **Step 2: Extend `listAgents` return type with `subagents`**

把现有 `listAgents()`（返回 `{ agents: AgentInfo[]; default: string | null }`）改为：

```typescript
export function listAgents() {
  return get<{ agents: AgentInfo[]; subagents: AgentInfo[]; default: string | null }>('/ai/chat/agents')
}
```

- [ ] **Step 3: Typecheck**

`cd E:/wsl/check/check-manage && npx vue-tsc --noEmit 2>&1 | tail -20` → 无新增错误（store 仍以 5 参调用 sendMessage，第 6 参有默认值；AiChatView 用 `r.agents`，新增 `subagents` 不破坏）

- [ ] **Step 4: Commit**

```bash
git add src/api/aiChat.ts
git commit -m "feat(ai-chat): AgentMention type, listAgents subagents, sendMessage agentMentions"
```

---

## Task 4: 前端纯函数 `agentMentions.ts` + 单测

**Files:**
- Create: `src/utils/agentMentions.ts`
- Test: `src/utils/__tests__/agentMentions.test.ts`

- [ ] **Step 1: Write the failing test**

创建 `src/utils/__tests__/agentMentions.test.ts`：

```typescript
import { describe, it, expect } from 'vitest'
import { activeMentionToken, parseAgentMentions } from '../agentMentions'

describe('activeMentionToken', () => {
  it('detects @ at start of input', () => {
    expect(activeMentionToken('@gen', 4)).toEqual({ query: 'gen', start: 0, end: 4 })
  })
  it('detects @ after whitespace mid-text', () => {
    expect(activeMentionToken('hi @ex', 6)).toEqual({ query: 'ex', start: 3, end: 6 })
  })
  it('returns empty query right after typing @', () => {
    expect(activeMentionToken('hi @', 4)).toEqual({ query: '', start: 3, end: 4 })
  })
  it('no token when @ is preceded by non-whitespace (email-like)', () => {
    expect(activeMentionToken('a@b', 3)).toBeNull()
  })
  it('no token when a space already follows the mention', () => {
    expect(activeMentionToken('@gen now', 8)).toBeNull()
  })
})

describe('parseAgentMentions', () => {
  const known = new Set(['general', 'explore'])
  it('parses one mention with offsets', () => {
    expect(parseAgentMentions('ask @general ok', known)).toEqual([
      { name: 'general', value: '@general', start: 4, end: 12 },
    ])
  })
  it('parses multiple and skips unknown names', () => {
    const out = parseAgentMentions('@general and @nope and @explore', known)
    expect(out.map((m) => m.name)).toEqual(['general', 'explore'])
  })
  it('requires whitespace (or start) before @', () => {
    expect(parseAgentMentions('a@general', known)).toEqual([])
  })
})
```

- [ ] **Step 2: Run, verify FAIL**

`cd E:/wsl/check/check-manage && npx vitest run src/utils/__tests__/agentMentions.test.ts` → FAIL（模块不存在）

- [ ] **Step 3: Implement**

创建 `src/utils/agentMentions.ts`：

```typescript
import type { AgentMention } from '@/api/aiChat'

/**
 * 找光标左侧正在输入的 @token：@ 必须位于行首或空白后，@ 与光标间只有 [\w-]。
 * 返回 { query, start(@位置), end(光标) }，无则 null。
 */
export function activeMentionToken(
  text: string,
  cursor: number,
): { query: string; start: number; end: number } | null {
  let i = cursor - 1
  while (i >= 0 && /[A-Za-z0-9_-]/.test(text[i])) i--
  if (i < 0 || text[i] !== '@') return null
  if (i > 0 && !/\s/.test(text[i - 1])) return null
  return { query: text.slice(i + 1, cursor), start: i, end: cursor }
}

/**
 * 扫描文本所有 @<name>（行首或空白后），保留 name ∈ knownNames，记录 value 与偏移 [start,end)。
 */
export function parseAgentMentions(text: string, knownNames: Set<string>): AgentMention[] {
  const out: AgentMention[] = []
  const re = /(^|\s)@([A-Za-z0-9_-]+)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    const name = m[2]
    if (!knownNames.has(name)) continue
    const start = m.index + m[1].length
    out.push({ name, value: '@' + name, start, end: start + name.length + 1 })
  }
  return out
}
```

- [ ] **Step 4: Run, verify PASS**

`cd E:/wsl/check/check-manage && npx vitest run src/utils/__tests__/agentMentions.test.ts` → 8 passed

- [ ] **Step 5: Typecheck + Commit**

`npx vue-tsc --noEmit 2>&1 | tail -10` → 干净

```bash
git add src/utils/agentMentions.ts src/utils/__tests__/agentMentions.test.ts
git commit -m "feat(ai-chat): @-mention token detection + parsing pure functions"
```

---

## Task 5: 前端 store — `subagents` 缓存 + `sendUserMessage` 解析透传

**Files:**
- Modify: `src/stores/aiChat.ts`
- Test: `src/stores/__tests__/aiChat.agent.test.ts`（既有文件，追加）

- [ ] **Step 1: Write the failing test**

在既有 `src/stores/__tests__/aiChat.agent.test.ts` 的 describe 内追加一个用例（该文件已 `vi.mock('@/api/aiChat', ...)` 并 import `sendMessage`；mock 工厂已含 `sendMessage`）：

```typescript
  it('sendUserMessage parses @subagent mentions and forwards them', async () => {
    const s = useAiChatStore()
    s.activeSessionId = 'sid1'
    s.messages['sid1'] = []
    s.attachments['sid1'] = []
    s.subagents = [{ name: 'general', description: '' }, { name: 'explore', description: '' }]
    await s.sendUserMessage('ask @general to help')
    const call = (sendMessage as any).mock.calls.at(-1)
    // sendMessage(sid, content, paths, model, agent, agentMentions)
    expect(call[5]).toEqual([{ name: 'general', value: '@general', start: 4, end: 12 }])
  })
```

> 若该 mock 工厂当前未导出某些被 store import 的符号导致报错，按 store 实际 import 列表补齐 `vi.fn()`（保持 `sendMessage` 的 mock 行为）。

- [ ] **Step 2: Run, verify FAIL**

`cd E:/wsl/check/check-manage && npx vitest run src/stores/__tests__/aiChat.agent.test.ts` → 新用例 FAIL（`subagents` 未定义 / 第 6 参未传）

- [ ] **Step 3: Add `subagents` state**

在 `src/stores/aiChat.ts` 的 State 接口里（`agentBySession: Record<string, string>` 附近）加：

```typescript
  subagents: import('@/api/aiChat').AgentInfo[]
```

> 若该文件已 `import type { AgentInfo } from '@/api/aiChat'`，直接用 `subagents: AgentInfo[]`；否则在顶部 import 处补 `AgentInfo`（与 `AgentMention` 一起从 `@/api/aiChat`）。

在 state 初始化处（`agentBySession: {} ...` 附近）加：

```typescript
      subagents: [] as AgentInfo[],
```

- [ ] **Step 4: Parse + forward mentions in `sendUserMessage`**

在 `src/stores/aiChat.ts` 顶部 import 处加：

```typescript
import { parseAgentMentions } from '@/utils/agentMentions'
```

在 `sendUserMessage` 中，找到：

```typescript
      const agent = this.agentBySession[sid] || ''
      const { messageId } = await sendMessage(sid, content, paths, model, agent)
```

改为：

```typescript
      const agent = this.agentBySession[sid] || ''
      const known = new Set(this.subagents.map((a) => a.name))
      const agentMentions = parseAgentMentions(content, known)
      const { messageId } = await sendMessage(sid, content, paths, model, agent, agentMentions)
```

- [ ] **Step 5: Run test + typecheck**

`cd E:/wsl/check/check-manage && npx vitest run src/stores/__tests__/aiChat.agent.test.ts` → all pass
`npx vue-tsc --noEmit 2>&1 | tail -10` → 干净

- [ ] **Step 6: Commit**

```bash
git add src/stores/aiChat.ts src/stores/__tests__/aiChat.agent.test.ts
git commit -m "feat(ai-chat): store caches subagents + forwards @ mentions on send"
```

---

## Task 6: 复用 CommandPalette — `prefix` prop + `agent` kind

**Files:**
- Modify: `src/components/ai-chat/CommandPalette.vue`

- [ ] **Step 1: Add `prefix` prop + `agent` kind**

把 `src/components/ai-chat/CommandPalette.vue` 的 script 改为（加 `'agent'` kind、`groupLabel.agent`、`prefix` prop，默认 `/`）：

```typescript
<script setup lang="ts">
import { computed } from 'vue'

export interface PaletteItem { kind: 'builtin' | 'command' | 'skill' | 'agent'; name: string; description: string }
const props = withDefaults(defineProps<{ items: PaletteItem[]; activeIndex: number; prefix?: string }>(), { prefix: '/' })
defineEmits<{ (e: 'select', item: PaletteItem): void }>()

const groupLabel: Record<PaletteItem['kind'], string> = { builtin: '内置', command: '命令', skill: '技能', agent: '智能体' }
const rows = computed(() => {
  const out: { header?: string; item?: PaletteItem; idx: number }[] = []
  let last = ''
  props.items.forEach((item, idx) => {
    if (item.kind !== last) { out.push({ header: groupLabel[item.kind], idx: -1 }); last = item.kind }
    out.push({ item, idx })
  })
  return out
})
</script>
```

把 template 里渲染名字的那行（当前 `<code class="palette-item__name">/{{ row.item!.name }}</code>`）改为用 prefix：

```vue
        <code class="palette-item__name">{{ prefix }}{{ row.item!.name }}</code>
```

- [ ] **Step 2: Typecheck**

`cd E:/wsl/check/check-manage && npx vue-tsc --noEmit 2>&1 | tail -15` → 无新增错误（AiChatView 仍只传 items/active-index，prefix 有默认值）

- [ ] **Step 3: Commit**

```bash
git add src/components/ai-chat/CommandPalette.vue
git commit -m "feat(ai-chat): CommandPalette supports prefix prop + agent kind"
```

---

## Task 7: AiChatView — 光标追踪 + mention palette + acceptItem 分流

**Files:**
- Modify: `src/views/ai-chat/AiChatView.vue`

- [ ] **Step 1: Fetch subagents into the store**

在 `fetchAgents()`（约 47-55 行，当前 `agents.value = r.agents`）里追加把 subagents 存入 store：

```typescript
    const r = await listAgents()
    agents.value = r.agents
    store.subagents = r.subagents
```

- [ ] **Step 2: Track cursor position**

在 script 里 `const input = ref('')` 附近加：

```typescript
const cursorPos = ref(0)
function syncCursor(e: Event) {
  const el = e.target as HTMLTextAreaElement
  cursorPos.value = el.selectionStart ?? input.value.length
}
```

import 处加 `activeMentionToken`：

```typescript
import { activeMentionToken } from '@/utils/agentMentions'
```

- [ ] **Step 3: Add the mention palette + unify open state**

在现有 `palette` computed（约 99-114 行，处理 `/`）之后新增 mention palette，并把 `paletteOpen` 改为「命令或 mention 任一非空」。新增：

```typescript
const mentionToken = computed(() => activeMentionToken(input.value, cursorPos.value))
const mentionPalette = computed<PaletteItem[]>(() => {
  const tok = mentionToken.value
  if (!tok) return []
  const q = tok.query.toLowerCase()
  return store.subagents
    .filter((a) => !q || a.name.toLowerCase().includes(q))
    .map((a) => ({ kind: 'agent' as const, name: a.name, description: a.description }))
})
// 命令 palette 优先级低于 mention：mention token 命中时只显示 mention
const activePalette = computed<PaletteItem[]>(() => (mentionToken.value ? mentionPalette.value : palette.value))
```

把现有 `const paletteOpen = computed(() => palette.value.length > 0)` 改为：

```typescript
const paletteOpen = computed(() => activePalette.value.length > 0)
watch(activePalette, () => { activeIndex.value = 0 })
```

（删除/替换原 `watch(palette, ...)` 为对 `activePalette` 的 watch。）

- [ ] **Step 4: Split acceptItem by kind**

找到现有 `acceptItem`（接受 `/` 命令的函数）。在其开头加 mention 分支：当 item.kind === 'agent'，把 mention token 的 `[start,end)` 替换为 `@name `（带尾空格），更新 input 与 cursorPos，然后 return：

```typescript
function acceptItem(item: PaletteItem) {
  if (item.kind === 'agent') {
    const tok = activeMentionToken(input.value, cursorPos.value)
    if (tok) {
      const before = input.value.slice(0, tok.start)
      const after = input.value.slice(tok.end)
      const insert = '@' + item.name + ' '
      input.value = before + insert + after
      cursorPos.value = before.length + insert.length
    }
    return
  }
  // ……以下保留原有命令/技能/内置处理逻辑不变……
}
```

把 onKey 里 palette 导航用的 `palette.value` 改为 `activePalette.value`（3 处：ArrowDown/ArrowUp 取模长度、Enter/Tab 取 `activePalette.value[activeIndex.value]`）。把模板里传给 `<CommandPalette>` 的 `:items="palette"` 改为 `:items="activePalette"`，并加 `:prefix="mentionToken ? '@' : '/'"`。

- [ ] **Step 5: Wire textarea cursor events**

在输入 `<textarea>`（约 681 行 `@keydown="onKey"` 处）补上光标同步事件：

```vue
              @keydown="onKey"
              @click="syncCursor"
              @keyup="syncCursor"
              @input="syncCursor"
```

（`@input` 与现有 `v-model` 不冲突；`syncCursor` 只读 selectionStart。）

- [ ] **Step 6: Typecheck**

`cd E:/wsl/check/check-manage && npx vue-tsc --noEmit 2>&1 | tail -20` → 无新增错误。修掉你引入的任何报错。

- [ ] **Step 7: Manual smoke（写入 commit message）**

`npm run dev:all`（OpenCode 在 4096）→ AI 助手选会话 → 输入框打 `@` 弹出 subagent 列表（general/explore…），↑↓选择、Enter 接受插入 `@general `；发一条「帮我 @general 调研」→ Network 里请求体 `agentMentions` 含 general，OpenCode 按点名委派。`/` 命令 palette 仍正常。

- [ ] **Step 8: Commit**

```bash
git add src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): @ subagent mention palette in composer"
```

---

## Self-Review 结果

- **Spec 覆盖**：`/ai/chat/agents` subagents(T1) / `send_prompt_async` agent_parts(T2) / `send_message` agentMentions(T2) / 前端 `AgentMention`+`listAgents`+`sendMessage`(T3) / 纯函数 token+parse(T4) / store subagents+解析透传(T5) / palette prefix+agent kind(T6) / AiChatView mention 补全+发送(T7) / 测试(T1,T2,T4,T5) 均有任务。✓
- **占位符**：无 TBD/TODO；每改动给完整代码。对依赖既有 mock 工厂/ store import 形状两处，明确「按文件实际对齐、保持断言意图」，非占位。✓
- **类型一致**：`AgentMention {name,value,start,end}`（T3 定义）→ parseAgentMentions 返回(T4)→ store 透传(T5)→ 后端 agent_parts(T2) 字段名一致；`sendMessage(...,agentMentions)` 第 6 参在 T3 定义、T5 调用、test 断言 `call[5]`；`listAgents` 返回 `subagents`(T3)↔后端(T1)↔store.subagents(T5/T7)；`PaletteItem.kind` 含 `'agent'`(T6) 与 AiChatView 构造一致(T7)。✓
