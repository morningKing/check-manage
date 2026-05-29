# AI 助手命令提示 + 执行 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 输入 `/` 弹出命令下拉（内置前端命令 + OpenCode 命令 + 技能），键盘可导航；命令可经后端真正执行（输出走现有 SSE），技能选中插入提示脚手架。

**Architecture:** 前端命令注册表 + `CommandPalette` 下拉 + AiChatView 接线；后端代理 OpenCode `GET /command`、`GET /skill` 并经 `POST /session/:id/command` 运行命令（回合像普通 prompt 一样在按目录 scope 的 SSE 上流出）。技能无运行接口 → 选中插脚手架，按普通消息发送由 agent 调用。

**Tech Stack:** Flask + psycopg2；Vue 3 `<script setup>` + TS + Element Plus + Pinia；Vitest/@vue/test-utils。

参考 spec：`docs/superpowers/specs/2026-05-29-ai-chat-command-palette-design.md`

**约定**：后端测试 `cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest <file> -v`；前端 `npx vitest run <file>`；构建 `npm run build`；全量 `npm test`。不要用 ES2022 API（如 `Array.prototype.at()`）——tsconfig lib 是 ES2020 且 build type-check 测试文件。

---

## File Structure

- `server/utils/opencode_client.py` — 加 `list_commands` / `list_skills` / `run_command`。
- `server/routes/ai_chat.py` — 加 `GET /sessions/:id/commands` + `POST /sessions/:id/command`。
- `src/api/aiChat.ts` — `PaletteCommand` 类型 + `getCommands` + `postCommand`。
- `src/components/ai-chat/chat-commands.ts` — 前端命令注册表 + `findFrontendCommand` + `parseCommandLine`（纯函数）。
- `src/components/ai-chat/CommandPalette.vue` — 下拉组件。
- `src/stores/aiChat.ts` — `paletteItems` 缓存 + `loadPaletteItems` + `isOpencodeCommand` + `runCommand`。
- `src/views/ai-chat/AiChatView.vue` — 下拉显隐/过滤/键盘导航/接受/`send()` 分流。

---

### Task 1: 真机冒烟（去风险，先做）

**目的**：确认 `POST /session/:id/command` 的回合在**按目录 scope 的 `/event` 流**上产出 `message.*`/`session.idle`，并确认 command body 的 `model` 形态（对象 vs 字符串）。不写产品代码；产出结论供 Task 2 用。

- [ ] **Step 1: 跑冒烟脚本**

前置：proxy 栈在跑（:8080 Flask、:4096 opencode、:3003 MCP）。在 `server/` 下用项目 python 跑：

```bash
cd server && python -c "
import json, time, threading, urllib.request, urllib.parse, urllib.error
BASE='http://127.0.0.1:8080'; OC='http://127.0.0.1:4096'
def jreq(m,p,t=None,b=None):
    d=json.dumps(b).encode() if b is not None else None
    r=urllib.request.Request(BASE+p,data=d,method=m); r.add_header('Content-Type','application/json')
    if t: r.add_header('Authorization','Bearer '+t)
    raw=urllib.request.urlopen(r,timeout=30).read(); return json.loads(raw) if raw.strip() else {}
tok=jreq('POST','/api/auth/login',b={'username':'admin','password':'admin123'})['token']
s=jreq('POST','/api/ai/chat/sessions',t=tok,b={}); sid=s['id']; ws=s['workspacePath']
# opencode ids
import psycopg2; from config import DB_CONFIG
c=psycopg2.connect(**DB_CONFIG);cur=c.cursor();cur.execute('SELECT opencode_session_id FROM ai_chat_sessions WHERE id=%s',(sid,));ocid=cur.fetchone()[0];c.close()
q=urllib.parse.quote(ws)
# list commands, pick a benign one (prefer one whose template is a simple prompt)
cmds=json.loads(urllib.request.urlopen(OC+f'/command?directory={q}',timeout=10).read().decode())
print('first 5 commands:', [c['name'] for c in cmds[:5]])
name=cmds[0]['name']
# subscribe directory-scoped events in a thread
seen={'msg':0,'idle':False}
def sse():
    resp=urllib.request.urlopen(f'{BASE}/api/ai/chat/sessions/{sid}/events?access_token={tok}',timeout=60)
    for raw in resp:
        if b'message.part' in raw or b'message.updated' in raw: seen['msg']+=1
        if b'session.idle' in raw: seen['idle']=True; break
threading.Thread(target=sse,daemon=True).start(); time.sleep(1)
# try model as OBJECT first
def run_command(body):
    r=urllib.request.Request(OC+f'/session/{ocid}/command?directory={q}',data=json.dumps(body).encode(),method='POST'); r.add_header('Content-Type','application/json')
    try:
        urllib.request.urlopen(r,timeout=30); return 'ok'
    except urllib.error.HTTPError as e: return f'{e.code}:{e.read().decode()[:200]}'
print('model=object ->', run_command({'command':name,'arguments':'','model':{'providerID':'mimo','modelID':'mimo-v2.5'}}))
time.sleep(20)
print('RESULT: msg_events=',seen['msg'],'idle=',seen['idle'])
"
```

- [ ] **Step 2: 记录结论**

报告：(a) `msg_events > 0 且 idle=True` 吗？（=命令回合走现有按目录 SSE，设计成立）。(b) `model=object` 是否 200？若返回 4xx 提示 model 类型，则改用 `'model': 'mimo/mimo-v2.5'`（字符串）重试并记录哪种可用。

- [ ] **Step 3: 结论决定 Task 2**

- 若 SSE 成立 → 继续 Task 2，`run_command` 用 Step 2 确认的 model 形态。
- 若 SSE **不**成立（无 message 事件）→ STOP，回报控制者调整设计（不要继续）。

（本任务无 commit。）

---

### Task 2: OpenCodeClient 命令/技能方法

**Files:** Modify `server/utils/opencode_client.py`; Test `server/tests/test_opencode_client.py`

- [ ] **Step 1: 写失败测试**（追加到 `test_opencode_client.py`）

```python
def test_list_commands_scopes_by_directory():
    fake = MagicMock(); fake.json.return_value = [{"name": "init", "description": "x"}]; fake.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.get", return_value=fake) as get:
        from utils.opencode_client import OpenCodeClient
        out = OpenCodeClient("http://x").list_commands("/ws")
    assert out == [{"name": "init", "description": "x"}]
    a, k = get.call_args; assert a[0].endswith("/command"); assert k["params"] == {"directory": "/ws"}


def test_list_skills_scopes_by_directory():
    fake = MagicMock(); fake.json.return_value = [{"name": "clawhub", "description": "y"}]; fake.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.get", return_value=fake) as get:
        from utils.opencode_client import OpenCodeClient
        out = OpenCodeClient("http://x").list_skills("/ws")
    assert out[0]["name"] == "clawhub"
    a, k = get.call_args; assert a[0].endswith("/skill"); assert k["params"] == {"directory": "/ws"}


def test_run_command_posts_command_and_arguments():
    fake = MagicMock(); fake.status_code = 202; fake.raise_for_status = MagicMock()
    with patch("utils.opencode_client.requests.post", return_value=fake) as post:
        from utils.opencode_client import OpenCodeClient
        OpenCodeClient("http://x").run_command("ses_1", "init", "do it", model="mimo/mimo-v2.5", directory="/ws")
    a, k = post.call_args
    assert a[0].endswith("/session/ses_1/command")
    assert k["params"] == {"directory": "/ws"}
    assert k["json"]["command"] == "init"
    assert k["json"]["arguments"] == "do it"
    assert k["json"]["model"] == {"providerID": "mimo", "modelID": "mimo-v2.5"}
```

> 注：`model` 断言按 Task 1 Step 2 确认的形态写；若 Task 1 确认是**字符串**，把最后一行改为 `assert k["json"]["model"] == "mimo/mimo-v2.5"`，且 Step 3 实现里相应改。

- [ ] **Step 2: 运行确认失败**

`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_opencode_client.py -k "list_commands or list_skills or run_command" -v` → FAIL（方法不存在）。

- [ ] **Step 3: 实现**（加到 `OpenCodeClient`，紧邻 `list_mcp`）

```python
def list_commands(self, directory: str = "") -> list:
    params = {"directory": directory} if directory else None
    resp = requests.get(self._url("/command"), params=params, timeout=self.timeout)
    resp.raise_for_status()
    return resp.json()

def list_skills(self, directory: str = "") -> list:
    params = {"directory": directory} if directory else None
    resp = requests.get(self._url("/skill"), params=params, timeout=self.timeout)
    resp.raise_for_status()
    return resp.json()

def run_command(self, opencode_session_id: str, command: str, arguments: str = "",
                model: str = "", directory: str = "") -> None:
    body = {"command": command, "arguments": arguments}
    if model and "/" in model:
        provider_id, model_id = model.split("/", 1)
        body["model"] = {"providerID": provider_id, "modelID": model_id}
    params = {"directory": directory} if directory else None
    resp = requests.post(
        self._url(f"/session/{opencode_session_id}/command"),
        params=params, json=body, timeout=self.timeout,
    )
    resp.raise_for_status()
```

- [ ] **Step 4: 运行确认通过** → 同 Step 2 命令，PASS。

- [ ] **Step 5: Commit**

```bash
git add server/utils/opencode_client.py server/tests/test_opencode_client.py
git commit -m "feat(opencode): list_commands/list_skills/run_command"
```

---

### Task 3: 后端路由 GET /commands + POST /command

**Files:** Modify `server/routes/ai_chat.py`; Test `server/tests/test_routes_ai_chat.py`

- [ ] **Step 1: 写失败测试**（追加；`setup` fixture yields `client, cursor, oc, dev_h, guest_h, tmp_path`）

```python
def test_list_commands_merges_commands_and_skills(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    oc.list_commands.return_value = [{'name': 'init', 'description': 'a', 'source': 'command', 'template': 't'}]
    oc.list_skills.return_value = [{'name': 'clawhub', 'description': 'b', 'location': 'L', 'content': 'C'}]
    resp = client.get('/ai/chat/sessions/sess_x/commands', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['commands'] == [{'name': 'init', 'description': 'a'}]
    assert body['skills'] == [{'name': 'clawhub', 'description': 'b'}]
    assert oc.list_commands.call_args[0][0] == '/tmp/ws'


def test_list_commands_degrades_on_error(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    oc.list_commands.side_effect = Exception('boom')
    oc.list_skills.return_value = [{'name': 'clawhub', 'description': 'b'}]
    resp = client.get('/ai/chat/sessions/sess_x/commands', headers=dev_h)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['commands'] == []
    assert body['skills'] == [{'name': 'clawhub', 'description': 'b'}]


def test_run_command_calls_opencode(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc_sess', 'active', '/tmp/ws')
    resp = client.post('/ai/chat/sessions/sess_x/command',
                       json={'command': 'init', 'arguments': 'go'}, headers=dev_h)
    assert resp.status_code == 202
    a, k = oc.run_command.call_args
    assert a[0] == 'oc_sess'
    assert a[1] == 'init'
    assert k.get('arguments') == 'go' or (len(a) > 2 and a[2] == 'go')
    assert k.get('directory') == '/tmp/ws'


def test_run_command_guest_403(setup):
    client, *_, guest_h, _ = setup
    resp = client.post('/ai/chat/sessions/sess_x/command', json={'command': 'init'}, headers=guest_h)
    assert resp.status_code == 403


def test_run_command_requires_command(setup):
    client, cursor, oc, dev_h, _, _ = setup
    cursor.fetchone.return_value = ('sess_x', 'user-1', 'oc', 'active', '/tmp/ws')
    resp = client.post('/ai/chat/sessions/sess_x/command', json={'command': '  '}, headers=dev_h)
    assert resp.status_code == 400
```

- [ ] **Step 2: 运行确认失败**

`cd server && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_routes_ai_chat.py -k "list_commands or run_command" -v` → FAIL（路由不存在）。

- [ ] **Step 3: 实现路由**（加到 `ai_chat.py`，紧邻 `list_mcp_services`）

```python
@ai_chat_bp.route('/sessions/<sid>/commands', methods=['GET'])
@login_required
def list_session_commands(sid):
    """List OpenCode commands + skills for the chat's command palette."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    client = OpenCodeClient(OPENCODE_BASE_URL)
    try:
        commands = [{'name': c['name'], 'description': c.get('description', '')}
                    for c in client.list_commands(sess[4])]
    except Exception:
        commands = []
    try:
        skills = [{'name': s['name'], 'description': s.get('description', '')}
                  for s in client.list_skills(sess[4])]
    except Exception:
        skills = []
    return jsonify({'commands': commands, 'skills': skills})


@ai_chat_bp.route('/sessions/<sid>/command', methods=['POST'])
@write_required
def run_session_command(sid):
    """Run an OpenCode command in the session; its turn streams via the SSE proxy."""
    user = flask_g.current_user
    sess = _load_session_for_user(sid, user['userId'])
    if not sess:
        return jsonify({'error': 'session not found', 'code': 'SESSION_NOT_FOUND'}), 404
    body = request.get_json(force=True)
    command = (body.get('command') or '').strip()
    arguments = (body.get('arguments') or '').strip()
    if not command:
        return jsonify({'error': 'command required', 'code': 'COMMAND_REQUIRED'}), 400
    # Persist a user-facing line so history shows what was run.
    shown = '/' + command + (' ' + arguments if arguments else '')
    msg_id = 'msg_' + secrets.token_hex(6)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ai_chat_messages (id, session_id, role, content) "
            "VALUES (%s, %s, 'user', %s)",
            (msg_id, sid, json.dumps([{'type': 'text', 'text': shown}])),
        )
    OpenCodeClient(OPENCODE_BASE_URL).run_command(
        sess[2], command, arguments, model=OPENCODE_MODEL, directory=sess[4],
    )
    return jsonify({'messageId': msg_id}), 202
```

> `run_command` 在测试里被 mock；上面 `test_run_command_calls_opencode` 的断言兼容位置参数/关键字两种传法（实现用位置参数 `run_command(sess[2], command, arguments, model=..., directory=...)`）。

- [ ] **Step 4: 运行确认通过** → 同 Step 2 命令 PASS；再跑整文件 `... -m pytest tests/test_routes_ai_chat.py -q` 无回归。

- [ ] **Step 5: Commit**

```bash
git add server/routes/ai_chat.py server/tests/test_routes_ai_chat.py
git commit -m "feat(ai-chat): GET /commands (list) + POST /command (run)"
```

---

### Task 4: 前端 api + 命令注册表/解析

**Files:** Modify `src/api/aiChat.ts`; Create `src/components/ai-chat/chat-commands.ts` + `src/components/ai-chat/__tests__/chat-commands.test.ts`

- [ ] **Step 1: api 加类型/函数**（`src/api/aiChat.ts`，near `getMcpServices`）

```ts
export interface PaletteCommand { name: string; description: string }
export function getCommands(id: string) {
  return get<{ commands: PaletteCommand[]; skills: PaletteCommand[] }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/commands`,
  )
}
export function postCommand(id: string, command: string, args: string) {
  return post<{ messageId: string }>(
    `/ai/chat/sessions/${encodeURIComponent(id)}/command`, { command, arguments: args },
  )
}
```

- [ ] **Step 2: 写失败测试** `src/components/ai-chat/__tests__/chat-commands.test.ts`

```ts
import { describe, it, expect } from 'vitest'
import { findFrontendCommand, parseCommandLine, FRONTEND_COMMANDS } from '@/components/ai-chat/chat-commands'

describe('parseCommandLine', () => {
  it('returns null for non-slash text', () => {
    expect(parseCommandLine('hello')).toBeNull()
    expect(parseCommandLine('  hi ')).toBeNull()
  })
  it('parses name and args', () => {
    expect(parseCommandLine('/mcps')).toEqual({ name: 'mcps', args: '' })
    expect(parseCommandLine('/init do the thing')).toEqual({ name: 'init', args: 'do the thing' })
  })
})

describe('findFrontendCommand', () => {
  it('finds /mcps and the /mcp alias, case-insensitively', () => {
    expect(findFrontendCommand('mcps')?.name).toBe('mcps')
    expect(findFrontendCommand('MCP')?.name).toBe('mcps')
    expect(findFrontendCommand('init')).toBeUndefined()
  })
  it('every registry command has name/description/run', () => {
    for (const c of FRONTEND_COMMANDS) {
      expect(typeof c.name).toBe('string')
      expect(typeof c.description).toBe('string')
      expect(typeof c.run).toBe('function')
    }
  })
})
```

- [ ] **Step 3: 运行确认失败** → `npx vitest run src/components/ai-chat/__tests__/chat-commands.test.ts` FAIL（模块不存在）。

- [ ] **Step 4: 实现** `src/components/ai-chat/chat-commands.ts`

```ts
import type { useAiChatStore } from '@/stores/aiChat'

export interface FrontendCommand {
  name: string
  description: string
  run: (store: ReturnType<typeof useAiChatStore>) => void | Promise<void>
}

export const FRONTEND_COMMANDS: FrontendCommand[] = [
  { name: 'mcps', description: '列出已配置的 MCP 服务及工具', run: (s) => s.showMcpServices() },
]

export function findFrontendCommand(name: string): FrontendCommand | undefined {
  const n = name.toLowerCase()
  const direct = FRONTEND_COMMANDS.find((c) => c.name === n)
  if (direct) return direct
  if (n === 'mcp') return FRONTEND_COMMANDS.find((c) => c.name === 'mcps')
  return undefined
}

export interface ParsedCommand { name: string; args: string }
export function parseCommandLine(text: string): ParsedCommand | null {
  const t = text.trim()
  if (!t.startsWith('/')) return null
  const sp = t.indexOf(' ')
  if (sp < 0) return { name: t.slice(1), args: '' }
  return { name: t.slice(1, sp), args: t.slice(sp + 1).trim() }
}
```

- [ ] **Step 5: 运行确认通过** → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/api/aiChat.ts src/components/ai-chat/chat-commands.ts src/components/ai-chat/__tests__/chat-commands.test.ts
git commit -m "feat(ai-chat): command registry + parseCommandLine + commands api"
```

---

### Task 5: store loadPaletteItems / isOpencodeCommand / runCommand

**Files:** Modify `src/stores/aiChat.ts`; Test `src/stores/__tests__/aiChat.command.test.ts` (create)

- [ ] **Step 1: 写失败测试** `src/stores/__tests__/aiChat.command.test.ts`

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/aiChat', () => ({
  getCommands: vi.fn(), postCommand: vi.fn(),
  getMcpServices: vi.fn(),
  createSession: vi.fn(), listSessions: vi.fn(), renameSession: vi.fn(), deleteSession: vi.fn(),
  getMessages: vi.fn(), sendMessage: vi.fn(), uploadFile: vi.fn(), listFiles: vi.fn(),
  getChanges: vi.fn(), createEventStream: vi.fn(() => ({ close() {} })),
}))
import { getCommands, postCommand } from '@/api/aiChat'
import { useAiChatStore } from '@/stores/aiChat'

beforeEach(() => setActivePinia(createPinia()))

describe('palette store', () => {
  it('loadPaletteItems caches commands and skills', async () => {
    const store = useAiChatStore()
    ;(getCommands as any).mockResolvedValue({ commands: [{ name: 'init', description: 'a' }], skills: [{ name: 'clawhub', description: 'b' }] })
    await store.loadPaletteItems('s1')
    expect(store.isOpencodeCommand('s1', 'init')).toBe(true)
    expect(store.isOpencodeCommand('s1', 'nope')).toBe(false)
  })

  it('runCommand pushes a user line and posts', async () => {
    const store = useAiChatStore()
    store.activeSessionId = 's1'; store.messages['s1'] = []
    ;(postCommand as any).mockResolvedValue({ messageId: 'm1' })
    await store.runCommand('s1', 'init', 'go')
    expect(postCommand).toHaveBeenCalledWith('s1', 'init', 'go')
    const last = store.messages['s1'][store.messages['s1'].length - 1]
    expect(last.role).toBe('user')
    expect((last.content[0] as any).text).toContain('/init go')
    expect(store.streaming['s1']).toBe(true)
  })
})
```

- [ ] **Step 2: 运行确认失败** → `npx vitest run src/stores/__tests__/aiChat.command.test.ts` FAIL.

- [ ] **Step 3: 实现**（`src/stores/aiChat.ts`）

扩展 `@/api/aiChat` import 增加 `getCommands, postCommand, type PaletteCommand`。State 加：`paletteItems: {} as Record<string, { commands: PaletteCommand[]; skills: PaletteCommand[] }>`。`openSession` 里 `loadFiles`/`loadChanges` 旁加 `this.loadPaletteItems(id)`。Actions 加：

```ts
async loadPaletteItems(id: string) {
  try {
    const { commands, skills } = await getCommands(id)
    this.paletteItems[id] = { commands, skills }
  } catch { /* non-fatal; palette shows builtin only */ }
},
isOpencodeCommand(id: string, name: string): boolean {
  const n = name.toLowerCase()
  return (this.paletteItems[id]?.commands ?? []).some((c) => c.name.toLowerCase() === n)
},
async runCommand(id: string, name: string, args: string) {
  const shown = '/' + name + (args ? ' ' + args : '')
  ;(this.messages[id] ?? (this.messages[id] = [])).push({
    id: 'local_' + Date.now(), role: 'user', content: [{ type: 'text', text: shown }],
  })
  this.streaming[id] = true
  this.thinking[id] = true
  this._resetStreamState(id)
  await postCommand(id, name, args)
},
```

- [ ] **Step 4: 运行确认通过** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stores/aiChat.ts src/stores/__tests__/aiChat.command.test.ts
git commit -m "feat(ai-chat): store loadPaletteItems/isOpencodeCommand/runCommand"
```

---

### Task 6: CommandPalette 组件

**Files:** Create `src/components/ai-chat/CommandPalette.vue` + `src/components/ai-chat/__tests__/CommandPalette.test.ts`

- [ ] **Step 1: 写失败测试** `src/components/ai-chat/__tests__/CommandPalette.test.ts`

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CommandPalette from '@/components/ai-chat/CommandPalette.vue'

const items = [
  { kind: 'builtin', name: 'mcps', description: '列出 MCP' },
  { kind: 'command', name: 'init', description: '建 AGENTS.md' },
  { kind: 'skill', name: 'clawhub', description: '技能' },
]

describe('CommandPalette', () => {
  it('renders grouped items and highlights activeIndex', () => {
    const w = mount(CommandPalette, { props: { items, activeIndex: 1 } })
    expect(w.text()).toContain('mcps')
    expect(w.text()).toContain('init')
    expect(w.text()).toContain('clawhub')
    expect(w.findAll('.palette-item')[1].classes()).toContain('active')
  })
  it('emits select on click', async () => {
    const w = mount(CommandPalette, { props: { items, activeIndex: 0 } })
    await w.findAll('.palette-item')[2].trigger('click')
    expect(w.emitted('select')![0][0]).toMatchObject({ name: 'clawhub', kind: 'skill' })
  })
  it('renders nothing when empty', () => {
    const w = mount(CommandPalette, { props: { items: [], activeIndex: 0 } })
    expect(w.find('.command-palette').exists()).toBe(false)
  })
})
```

- [ ] **Step 2: 运行确认失败** → FAIL（组件不存在）。

- [ ] **Step 3: 实现** `src/components/ai-chat/CommandPalette.vue`

```vue
<script setup lang="ts">
import { computed } from 'vue'

export interface PaletteItem { kind: 'builtin' | 'command' | 'skill'; name: string; description: string }
const props = defineProps<{ items: PaletteItem[]; activeIndex: number }>()
defineEmits<{ (e: 'select', item: PaletteItem): void }>()

const groupLabel: Record<PaletteItem['kind'], string> = { builtin: '内置', command: '命令', skill: '技能' }
// flat list keeps activeIndex simple; we render group headers inline by detecting kind changes
const withHeaders = computed(() => {
  const out: { header?: string; item?: PaletteItem; idx: number }[] = []
  let last = ''
  props.items.forEach((item, idx) => {
    if (item.kind !== last) { out.push({ header: groupLabel[item.kind], idx: -1 }); last = item.kind }
    out.push({ item, idx })
  })
  return out
})
</script>

<template>
  <div v-if="items.length" class="command-palette">
    <template v-for="(row, i) in withHeaders" :key="i">
      <div v-if="row.header" class="palette-group">{{ row.header }}</div>
      <div
        v-else
        class="palette-item" :class="{ active: row.idx === activeIndex }"
        @mousedown.prevent="$emit('select', row.item!)"
      >
        <code class="palette-item__name">/{{ row.item!.name }}</code>
        <span class="palette-item__desc">{{ row.item!.description }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped lang="scss">
.command-palette {
  position: absolute; bottom: 100%; left: 0; right: 0; margin-bottom: 6px;
  max-height: 280px; overflow-y: auto;
  background: var(--el-bg-color); border: 1px solid var(--el-border-color);
  border-radius: 8px; box-shadow: var(--el-box-shadow-light); z-index: 10; padding: 4px;
}
.palette-group { padding: 4px 8px; font-size: 12px; color: var(--el-text-color-secondary); }
.palette-item {
  display: flex; align-items: baseline; gap: 8px; padding: 6px 8px;
  border-radius: 6px; cursor: pointer;
  &.active, &:hover { background: var(--el-fill-color); }
}
.palette-item__name { font-family: var(--el-font-family-mono, monospace); }
.palette-item__desc { font-size: 12px; color: var(--el-text-color-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
```

> 用 `@mousedown.prevent` 而非 `@click`：避免点击时输入框 blur 抢先关闭下拉。

- [ ] **Step 4: 运行确认通过** → PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/components/ai-chat/CommandPalette.vue src/components/ai-chat/__tests__/CommandPalette.test.ts
git commit -m "feat(ai-chat): CommandPalette dropdown component"
```

---

### Task 7: AiChatView 接线（显隐/过滤/键盘/接受/分流）+ 构建

**Files:** Modify `src/views/ai-chat/AiChatView.vue`

- [ ] **Step 1: imports + state**

`<script setup>` 顶部加：
```ts
import CommandPalette, { type PaletteItem } from '@/components/ai-chat/CommandPalette.vue'
import { findFrontendCommand, parseCommandLine, FRONTEND_COMMANDS } from '@/components/ai-chat/chat-commands'
```
新增响应式：
```ts
const activeIndex = ref(0)
const palette = computed<PaletteItem[]>(() => {
  const t = input.value.trim()
  if (!t.startsWith('/')) return []
  const q = t.slice(1).split(' ')[0].toLowerCase()
  const sid = activeId.value
  const cached = sid ? store.paletteItems[sid] : undefined
  const builtin: PaletteItem[] = FRONTEND_COMMANDS.map(c => ({ kind: 'builtin', name: c.name, description: c.description }))
  const commands: PaletteItem[] = (cached?.commands ?? []).map(c => ({ kind: 'command', name: c.name, description: c.description }))
  const skills: PaletteItem[] = (cached?.skills ?? []).map(s => ({ kind: 'skill', name: s.name, description: s.description }))
  return [...builtin, ...commands, ...skills].filter(it => !q || it.name.toLowerCase().includes(q))
})
const paletteOpen = computed(() => palette.value.length > 0)
watch(palette, () => { activeIndex.value = 0 })
```
（确保 `ref`/`computed`/`watch` 已 import 自 vue。）

- [ ] **Step 2: 接受逻辑 + send 分流**

替换 `send()` 与 `onKey`：
```ts
function acceptItem(item: PaletteItem) {
  if (item.kind === 'skill') input.value = '使用 `' + item.name + '` 技能:'
  else input.value = '/' + item.name + ' '
  activeIndex.value = 0
}

async function send() {
  if (!canSend.value) return
  const text = input.value.trim()
  input.value = ''
  if (!activeId.value) await newSession()
  const sid = activeId.value!
  const parsed = parseCommandLine(text)
  if (parsed) {
    const fc = findFrontendCommand(parsed.name)
    if (fc) { await fc.run(store); return }
    if (store.isOpencodeCommand(sid, parsed.name)) {
      try { await store.runCommand(sid, parsed.name, parsed.args) } catch { ElMessage.error('执行失败') }
      return
    }
    // unknown /xxx → fall through to a normal message
  }
  try { await store.sendUserMessage(text) } catch { ElMessage.error('发送失败') }
}

function onKey(e: Event) {
  const ev = e as KeyboardEvent
  if (paletteOpen.value) {
    if (ev.key === 'ArrowDown') { ev.preventDefault(); activeIndex.value = (activeIndex.value + 1) % palette.value.length; return }
    if (ev.key === 'ArrowUp') { ev.preventDefault(); activeIndex.value = (activeIndex.value - 1 + palette.value.length) % palette.value.length; return }
    if (ev.key === 'Enter' || ev.key === 'Tab') { ev.preventDefault(); acceptItem(palette.value[activeIndex.value]); return }
  }
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); send() }
}
```
> 不做 Esc 特殊关闭（`paletteOpen` 由 `input` 派生，用户删掉开头的 `/` 即关）—— YAGNI。

- [ ] **Step 3: 模板挂载下拉**

在 `<ElInput v-model="input" ...>` 所在的输入容器上确保有 `position: relative`（容器类，如 `.ai-chat__composer` 或其内层）。在该容器内、输入框上方插入：
```html
<CommandPalette :items="palette" :active-index="activeIndex" @select="acceptItem" />
```
若最近的定位容器没有 `position: relative`，给它加（scoped 样式里）。

- [ ] **Step 4: 构建 + 全量测试**

`npm run build` → 通过（注意：`palette`/`paletteItems` 类型一致；`PaletteItem` 从组件导出）。
`npm test` → 全绿（含前述 Task 4/5/6 新测试）。

- [ ] **Step 5: Commit**

```bash
git add src/views/ai-chat/AiChatView.vue
git commit -m "feat(ai-chat): command palette dropdown + / command dispatch in AiChatView"
```

---

## 真机验证（全部任务后）

1. 重启后端 + 重建前端 / 起 proxy（新路由 + 新前端生效）。
2. 输入 `/` → 出现下拉（内置/命令/技能三组），↑↓ 高亮、Enter/Tab 接受。
3. 选 `/mcps` 回车 → MCP 服务块。
4. 选某 OpenCode 命令 → 输入框 `/<name> `，补参数回车 → 看到回合流式输出（验证 Task 1 的结论在真链路成立）。
5. 选某技能 → 输入框出现「使用 \`<name>\` 技能:」脚手架；补充后回车 → agent 调用该技能。
6. 边界：opencode 不可用时下拉只剩内置；未知 `/xxx` 当普通消息发送。

## Self-Review 备注

- **Spec 覆盖**：命令/技能列举(T2/T3)、命令执行(T2/T3/T5)、注册表+解析(T4)、store(T5)、下拉(T6)、接线+分流+键盘(T7)、风险冒烟(T1)。
- **类型一致**：`PaletteCommand{name,description}`（api/store）、`PaletteItem{kind,name,description}`（组件/视图）、`ParsedCommand{name,args}`；`runCommand(id,name,args)` / `postCommand(id,command,args)` / `isOpencodeCommand(id,name)` 签名前后一致。
- **风险**：Task 1 先验证命令回合走按目录 SSE + model 形态；不成立则停并回设计。
- **YAGNI**：无模糊排序、无参数表单、Esc 不特殊处理、技能不解析参数。
