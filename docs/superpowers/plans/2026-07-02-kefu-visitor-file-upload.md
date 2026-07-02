# 智能客服访客文件上传 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 让访客在客服对话框上传文件、Agent 能读取——补齐访客页前端接线（后端与 Agent 读取已就绪，本特性不改后端）。

**Architecture:** `kefuPublic.ts` 加 `uploadKefuFile` + `sendKefuMessage` 透传 `attachments`；`KefuChatPage.vue` 加 📎 上传按钮、待发送附件 chip、发送带附件、用户气泡渲染文件名。复用现有公开上传端点 `POST /kefu/sessions/<sid>/files`（返回 `{name,path,size}`）与 send_message 的 attachments 支持；Agent 经 MCP `read_upload`（在 kefu 只读白名单内）读取。

**Tech Stack:** Vue 3 + Element Plus + TypeScript；原生 `fetch`（multipart 上传）；Vitest + Playwright。

## Global Constraints

- **不改后端**：上传端点 `POST /kefu/sessions/<sid>/files` 已校验 20MB / 扩展名白名单 / 路径安全并返回 `{name,path,size}`；`send_message` 已接收 `attachments`（rel 路径数组）并注入路径提示；Agent 经 `read_upload` 读取。仅做前端。
- 每条消息支持**多个**附件；允许仅附件无文字发送。
- 访客匿名：所有请求带 `X-Visitor-Id`（`getVisitorId()`）。上传用原生 `fetch` 到 `/api/kefu/sessions/<sid>/files`（vite 重写 `/api`），带 `X-Visitor-Id` 头。
- 复用后端校验（413 超限 / 415 类型）——前端仅弹出后端返回的错误消息，不放松校验。
- 前端 `npm run build`（vue-tsc 严格 noUnusedLocals/noUnusedParameters）必须过；Vitest 绿。提交用中文 `feat:` 前缀。

---

### Task 1: `kefuPublic.ts` — 上传函数 + 附件透传

**Files:**
- Modify: `src/api/kefuPublic.ts`
- Test: `src/api/__tests__/kefuPublic.test.ts`（扩现有）

**Interfaces:**
- Produces:
  - `uploadKefuFile(sid: string, file: File): Promise<{ name: string; path: string; size: number }>`（multipart POST `/api/kefu/sessions/${sid}/files`，带 `X-Visitor-Id`；非 2xx 抛 Error(后端 error 消息)）。
  - `sendKefuMessage(sid, content, attachments: string[] = [])`（body 加 `attachments`；默认 `[]` 兼容既有调用）。

- [ ] **Step 1: 写失败测试**（现有测试文件已在用 FakeEventSource；这里 mock `fetch` 与 `@/utils/request`）

```ts
// 追加到 src/api/__tests__/kefuPublic.test.ts
describe('uploadKefuFile', () => {
  it('POSTs multipart to the files endpoint with X-Visitor-Id and returns json', async () => {
    localStorage.setItem('kefu:visitor_id', 'vid-1')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ name: 'a.txt', path: 'uploads/a.txt', size: 3 }) })
    ;(globalThis as any).fetch = fetchMock
    const { uploadKefuFile } = await import('../kefuPublic')
    const res = await uploadKefuFile('sess_1', new File(['abc'], 'a.txt', { type: 'text/plain' }))
    expect(res.path).toBe('uploads/a.txt')
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/kefu/sessions/sess_1/files')
    expect(opts.method).toBe('POST')
    expect(opts.headers['X-Visitor-Id']).toBe('vid-1')
    expect(opts.body).toBeInstanceOf(FormData)
  })

  it('throws with backend error message on non-2xx', async () => {
    ;(globalThis as any).fetch = vi.fn().mockResolvedValue({ ok: false, json: async () => ({ error: '文件超过 20MB 上限' }) })
    const { uploadKefuFile } = await import('../kefuPublic')
    await expect(uploadKefuFile('sess_1', new File(['x'], 'x.bin'))).rejects.toThrow('文件超过 20MB 上限')
  })
})

describe('sendKefuMessage attachments', () => {
  it('passes attachments in the body', async () => {
    const post = vi.fn().mockResolvedValue({ messageId: 'm1' })
    vi.doMock('@/utils/request', () => ({ get: vi.fn(), post }))
    vi.resetModules()
    const mod = await import('../kefuPublic')
    await mod.sendKefuMessage('sess_1', 'hi', ['uploads/a.txt'])
    const [, body] = post.mock.calls[0]
    expect(body).toEqual({ content: 'hi', attachments: ['uploads/a.txt'] })
  })
})
```
> 注：`sendKefuMessage` 的 mock 需要模块隔离；若 `vi.doMock`+`resetModules` 与现有测试的 import 冲突，改为断言实现层——把 `sendKefuMessage` 的 attachments 透传做成可直接单测的纯逻辑，或在测试顶部统一 `vi.mock('@/utils/request')`。实现者按现有测试文件既有的 mock 风格调整，断言目标不变：body 含 `content` + `attachments`。

- [ ] **Step 2: 运行确认失败** — `npx vitest run src/api/__tests__/kefuPublic.test.ts` → FAIL

- [ ] **Step 3: 改 `src/api/kefuPublic.ts`**

```ts
export function sendKefuMessage(sid: string, content: string, attachments: string[] = []) {
  return post<{ messageId: string }>(`/kefu/sessions/${sid}/messages`, { content, attachments }, vh())
}

export async function uploadKefuFile(sid: string, file: File): Promise<{ name: string; path: string; size: number }> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`/api/kefu/sessions/${encodeURIComponent(sid)}/files`, {
    method: 'POST',
    headers: { 'X-Visitor-Id': getVisitorId() },  // do NOT set Content-Type; browser sets multipart boundary
    body: fd,
  })
  if (!res.ok) {
    const e = await res.json().catch(() => ({}))
    throw new Error(e.error || '上传失败')
  }
  return res.json()
}
```
（替换现有 `sendKefuMessage`；`uploadKefuFile` 新增。`getVisitorId`/`vh`/`post` 均已在文件内。）

- [ ] **Step 4: 运行确认通过 + 构建** — `npx vitest run src/api/__tests__/kefuPublic.test.ts` → PASS；`npm run build` → clean。

- [ ] **Step 5: 提交**

```bash
git add src/api/kefuPublic.ts src/api/__tests__/kefuPublic.test.ts
git commit -m "feat(kefu): 访客上传 API uploadKefuFile + 发消息透传附件"
```

---

### Task 2: `KefuChatPage.vue` — 上传按钮 + 附件 chip + 发送 + 气泡渲染

**Files:**
- Modify: `src/views/kefu/KefuChatPage.vue`
- Test: `src/views/kefu/__tests__/KefuChatPage.test.ts`（扩现有；mock 里加 uploadKefuFile）

**Interfaces:**
- Consumes: `kefuPublic.uploadKefuFile`、`sendKefuMessage(sid,content,attachments)`。
- 状态 `pending = ref<{name:string;path:string}[]>([])`。方法 `onPickFiles(files: FileList)`（逐个上传→推 pending，失败 toast）、`removePending(i)`、`fileParts(content)`（取 `type==='file'` 部件）。`send()` 带 `pending` 路径并在成功派发后清空 pending；仅附件也可发。

- [ ] **Step 1: 写失败测试**（扩 `KefuChatPage.test.ts`；给 `@/api/kefuPublic` mock 加 `uploadKefuFile`）

```ts
// mock 补：uploadKefuFile: vi.fn().mockResolvedValue({ name:'a.txt', path:'uploads/a.txt', size:3 })
it('uploads picked files into pending', async () => {
  const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
  await flushPromises()
  await (w.vm as any).onPickFiles([new File(['abc'],'a.txt')])
  expect((w.vm as any).pending).toEqual([{ name: 'a.txt', path: 'uploads/a.txt' }])
})

it('send passes pending attachments and clears them', async () => {
  const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
  await flushPromises()
  ;(w.vm as any).pending = [{ name: 'a.txt', path: 'uploads/a.txt' }]
  ;(w.vm as any).draft = '看看这个'
  await (w.vm as any).send()
  expect(api.sendKefuMessage).toHaveBeenCalledWith('sess_1', '看看这个', ['uploads/a.txt'])
  expect((w.vm as any).pending).toEqual([])
})

it('can send with only attachments (no text)', async () => {
  const w = mount(KefuChatPage, { props: { slug: 's' }, global: { stubs } })
  await flushPromises()
  ;(w.vm as any).pending = [{ name: 'a.txt', path: 'uploads/a.txt' }]
  ;(w.vm as any).draft = ''
  await (w.vm as any).send()
  expect(api.sendKefuMessage).toHaveBeenCalledWith('sess_1', '', ['uploads/a.txt'])
})
```

- [ ] **Step 2: 运行确认失败** — `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts` → FAIL

- [ ] **Step 3: 改 `KefuChatPage.vue`**

模板——输入区（`.kefu-input` footer）改为附件区 + 输入行：
```vue
      <footer class="kefu-input">
        <div v-if="pending.length" class="pending">
          <span v-for="(p,i) in pending" :key="i" class="pending-chip">📎 {{ p.name }} <b @click="removePending(i)">✕</b></span>
        </div>
        <div class="input-row">
          <button class="attach-btn" title="上传文件" @click="fileInput?.click()">📎</button>
          <input ref="fileInput" type="file" multiple class="hidden-file" @change="onFileChange" />
          <el-input v-model="draft" type="textarea" :rows="2" placeholder="输入你的问题…" @keydown.enter="onEnter" />
          <el-button type="primary" :disabled="(!draft.trim() && pending.length===0) || sending" @click="send">发送</el-button>
        </div>
      </footer>
```
用户气泡渲染文件部件（在用户消息分支加）：
```vue
        <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
          <MarkdownView v-if="m.role==='assistant'" :text="plainText(m.content)" />
          <template v-else>
            <span v-if="plainText(m.content)" class="user-text">{{ plainText(m.content) }}</span>
            <span v-for="(f,i) in fileParts(m.content)" :key="i" class="file-chip">📎 {{ f.name }}</span>
          </template>
        </div>
```
脚本增量：
```ts
import { uploadKefuFile } from '@/api/kefuPublic'   // 或并入现有 `import * as api`
const pending = ref<{ name: string; path: string }[]>([])
const fileInput = ref<HTMLInputElement | null>(null)

function fileParts(content: any) { return normalize(content).filter((p: any) => p.type === 'file') }
async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) await onPickFiles(Array.from(input.files))
  input.value = ''  // allow re-picking the same file
}
async function onPickFiles(files: File[]) {
  for (const f of files) {
    try {
      const r = await api.uploadKefuFile(sessionId.value, f)
      pending.value.push({ name: r.name, path: r.path })
    } catch (err: any) { ElMessage.error(err?.message || '上传失败') }
  }
}
function removePending(i: number) { pending.value.splice(i, 1) }
```
`send()` 改为带附件 + 清 pending（放开纯附件可发）：
```ts
async function send() {
  const text = draft.value.trim()
  const atts = pending.value.map(p => p.path)
  if ((!text && atts.length === 0) || sending.value) return
  draft.value = ''; sending.value = true
  const parts: any[] = []
  if (text) parts.push({ type: 'text', text })
  for (const p of pending.value) parts.push({ type: 'file', name: p.name, path: p.path })
  messages.value.push({ id: 'local_' + Date.now(), role: 'user', content: parts, createdAt: null })
  pending.value = []
  await scrollDown()
  try { await api.sendKefuMessage(sessionId.value, text, atts) }
  catch { ElMessage.error('发送失败，请稍后重试'); sending.value = false }
}
```
> `askBubble`/`onEscalate` 仍调 `send()`（它们设 `draft` 后调用，attachments 为空——不受影响）。若 `api.uploadKefuFile` 未在 `import * as api` 覆盖内，改为从 `@/api/kefuPublic` 具名导入并用 `api.uploadKefuFile`（保持与测试 mock 的 `api.uploadKefuFile` 一致——测试 mock 的是 `@/api/kefuPublic` 模块，故用 `api.` 前缀调用）。`defineExpose` 增加 `pending, onPickFiles, send, fileParts`。
> 样式补：`.hidden-file{display:none}`、`.pending-chip`/`.file-chip`/`.attach-btn` 简单卡片样式。

- [ ] **Step 4: 运行测试 + 构建** — `npx vitest run src/views/kefu/__tests__/KefuChatPage.test.ts` → PASS；`npm run build` → vue-tsc clean（注意 `fileInput`/`pending`/`fileParts` 都被模板用到，无 unused）。

- [ ] **Step 5: 提交**

```bash
git add src/views/kefu/KefuChatPage.vue src/views/kefu/__tests__/KefuChatPage.test.ts
git commit -m "feat(kefu): 访客页文件上传（📎按钮/附件chip/带附件发送/气泡渲染）"
```

---

### Task 3: 端到端 Playwright + 文档

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`

- [ ] **Step 1: Playwright 手测（必做）** — 后端 + OpenCode + MCP + vite 均在跑。管理员有一个客服实例（如 `demo`）。**匿名**访问 `/kefu/<slug>`：
  1. 点 📎 选一个文本文件（内容含一句可识别标记，如 `MAGIC-TOKEN-123`）。
  2. 附件 chip 出现在输入框上方；输入「请读取并复述这个文件里的标记」→ 发送。
  3. 用户气泡显示文字 + 文件名 chip；Agent 回复中体现读到了文件内容（出现 `MAGIC-TOKEN-123` 或其转述）。
  4. 交叉核对：会话 workspace 的 `uploads/` 下存在该文件（或 DB `ai_chat_messages` 该用户消息含 file 部件）。
  截图存 `.playwright-mcp/kefu-upload.png`。
  > 若 Agent 未主动调用 read_upload，可在消息里明确「请用工具读取上传的文件」；验证目标是「能读到」，非「每次自动读」。

- [ ] **Step 2: 更新文档** — `docs/user-guide/ai/smart-customer-service.md` 访客体验小节补：可点 📎 上传文件（多个、≤20MB、白名单类型），Agent 可读取分析。

- [ ] **Step 3: 提交**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 访客文件上传使用说明"
```

---

## Self-Review

**Spec coverage（对照 spec §3、§5）：**
- §3.1 `uploadKefuFile` + `sendKefuMessage` 附件 → Task 1 ✓；§3.2 上传按钮/pending chip/纯附件可发/气泡文件渲染 → Task 2 ✓；§5 测试（Vitest + Playwright + 文档）→ Task 1-3 ✓。不改后端（spec §1/§4）→ 计划无后端任务 ✓。

**Placeholder scan:** 无 TBD/TODO。Task 1 Step 1 与 Task 2 关于 mock 风格的两处注记是「按现有测试文件既有 mock 风格调整」的具体指引（断言目标明确），非占位。Playwright 为人工验证清单，断言点具体（MAGIC-TOKEN-123 出现在回复）。

**Type consistency:** `uploadKefuFile(sid,file)→{name,path,size}` 在 Task 1 定义、Task 2 消费一致；`pending` 元素 `{name,path}` 在 send 构造 file 部件 `{type:'file',name,path}` 与 `fileParts` 过滤一致；`sendKefuMessage(sid,content,attachments)` 三参签名 Task 1 定义、Task 2 调用一致。

---

## 后续
- 图片附件内联缩略图预览。
- 上传进度条 / 拖拽上传。
- 这些均为体验增强，非本增量必需。
