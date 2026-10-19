# 人工接管 子项目 B2：管理端接管控制台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 客服设置页加第 3 个 tab「人工接管」——左右主从控制台（会话队列 + 对话 + 接管/释放 + 人工回复），消费已上线的管理端接口。

**Architecture:** 新组件 `KefuTakeoverPanel.vue`（props `instanceId`）承载控制台；`api/kefu.ts` 补 5 个会话接口；`KefuManager.vue` 加一个 `el-tab-pane` 挂载面板。5s 轮询队列+对话。

**Tech Stack:** Vue 3 `<script setup>` + Element Plus + Vitest（`vi.useFakeTimers` 测轮询）。

## Global Constraints

- **纯前端**，不改后端/B1/其它两 tab。队列**限当前实例**（`instance=instanceId`）。
- 过滤：`all`→`{status:'active'}`、`needs_human`→`{needs_human:true}`、`takeover`→`{takeover:true}`（均带 `instance`）；默认 `all`。
- `@/utils/request` 的 `get<T>(url, params?, config?)` 把第二参作 axios query 发送；`listSessions` 用 `get('/admin/kefu/sessions', params)`。
- 人工回复**仅接管中**（`selected.human_takeover`）可发；对话区 `meta.author==='human'` 显「人工」徽标。
- 轮询队列+当前对话各 5000ms，失败 `.catch(()=>{})` 静默；`onBeforeUnmount` 清 timer。
- 颜色用 Element Plus 主题变量。前端测试从 repo 根 `npx vitest run <file>`；构建 `npm run build`。提交只 `git add` 该任务确切文件。

---

### Task 1: API + `KefuTakeoverPanel.vue`

**Files:**
- Modify: `src/api/kefu.ts`（加 5 个会话函数）
- Create: `src/components/kefu/KefuTakeoverPanel.vue`
- Create: `src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`

**Interfaces:**
- Produces: `listSessions(params)`/`getSessionMessages(sid)`/`takeoverSession(sid)`/`releaseSession(sid)`/`humanReply(sid, content)`（`@/api/kefu`）；组件 `KefuTakeoverPanel`（props `{ instanceId: string }`），expose `sessions/filter/selectedSid/messages/replyDraft/selected/selectSession/takeover/release/sendReply/loadQueue`。

- [ ] **Step 1: 写失败测试**

`src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const mockList = vi.fn(), mockMsgs = vi.fn(), mockTakeover = vi.fn(), mockRelease = vi.fn(), mockReply = vi.fn()
vi.mock('@/api/kefu', () => ({
  listSessions: (...a: any[]) => mockList(...a),
  getSessionMessages: (...a: any[]) => mockMsgs(...a),
  takeoverSession: (...a: any[]) => mockTakeover(...a),
  releaseSession: (...a: any[]) => mockRelease(...a),
  humanReply: (...a: any[]) => mockReply(...a),
}))
vi.mock('element-plus', () => ({ ElMessage: { error: vi.fn(), success: vi.fn() } }))
import KefuTakeoverPanel from '../KefuTakeoverPanel.vue'

const stubs = {
  'el-radio-group': { template: '<div><slot/></div>', props: ['modelValue'] },
  'el-radio-button': { template: '<button><slot/></button>', props: ['label'] },
  'el-button': { template: `<button :disabled="disabled" @click="$emit('click')"><slot/></button>`, props: ['type', 'size', 'text', 'disabled'] },
  'el-input': { template: `<input :disabled="disabled" :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />`, props: ['modelValue', 'disabled', 'placeholder'] },
}
const SAMPLE = [{ id: 'sess_1', visitor_id: 'v-abcdef12', needs_human: true, human_takeover: false, human_agent_id: null, status: 'active', last_message: 'hi', last_active_at: null }]
const mountP = (instanceId = 'kf_1') => mount(KefuTakeoverPanel, { props: { instanceId }, global: { stubs } })

describe('KefuTakeoverPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockList.mockResolvedValue({ sessions: SAMPLE })
    mockMsgs.mockResolvedValue({ messages: [{ id: 'm1', role: 'assistant', content: [{ type: 'text', text: 'hello' }], meta: { author: 'human' }, createdAt: null }] })
    mockTakeover.mockResolvedValue({ humanTakeover: true })
    mockRelease.mockResolvedValue({ humanTakeover: false })
    mockReply.mockResolvedValue({ messageId: 'x' })
  })

  it('loads queue scoped to instance on mount', async () => {
    const w = mountP('kf_1'); await flushPromises()
    expect(mockList.mock.calls[0][0].instance).toBe('kf_1')
    expect((w.vm as any).sessions).toHaveLength(1)
  })

  it('filter=needs_human queries needs_human:true', async () => {
    const w = mountP(); await flushPromises()
    ;(w.vm as any).filter = 'needs_human'; await flushPromises()
    expect(mockList.mock.calls.at(-1)[0].needs_human).toBe(true)
  })

  it('selectSession loads its messages', async () => {
    const w = mountP(); await flushPromises()
    await (w.vm as any).selectSession('sess_1')
    expect(mockMsgs).toHaveBeenCalledWith('sess_1')
    expect((w.vm as any).messages).toHaveLength(1)
  })

  it('takeover calls api then refreshes queue+conv', async () => {
    const w = mountP(); await flushPromises()
    await (w.vm as any).selectSession('sess_1')
    mockList.mockClear(); mockMsgs.mockClear()
    await (w.vm as any).takeover()
    expect(mockTakeover).toHaveBeenCalledWith('sess_1')
    expect(mockList).toHaveBeenCalled()
    expect(mockMsgs).toHaveBeenCalled()
  })

  it('release calls api', async () => {
    const w = mountP(); await flushPromises()
    await (w.vm as any).selectSession('sess_1')
    await (w.vm as any).release()
    expect(mockRelease).toHaveBeenCalledWith('sess_1')
  })

  it('sendReply only when the selected session is taken over', async () => {
    const w = mountP(); await flushPromises()
    await (w.vm as any).selectSession('sess_1')
    ;(w.vm as any).replyDraft = 'hi'
    await (w.vm as any).sendReply()
    expect(mockReply).not.toHaveBeenCalled()             // not taken over
    mockList.mockResolvedValue({ sessions: [{ ...SAMPLE[0], needs_human: false, human_takeover: true }] })
    await (w.vm as any).loadQueue()
    ;(w.vm as any).replyDraft = 'hello agent'
    await (w.vm as any).sendReply()
    expect(mockReply).toHaveBeenCalledWith('sess_1', 'hello agent')
    expect((w.vm as any).replyDraft).toBe('')
  })

  it('polls queue every 5s and stops on unmount', async () => {
    vi.useFakeTimers()
    const w = mountP(); await flushPromises()
    mockList.mockClear()
    vi.advanceTimersByTime(5000); await flushPromises()
    expect(mockList).toHaveBeenCalled()
    w.unmount(); mockList.mockClear()
    vi.advanceTimersByTime(10000); await flushPromises()
    expect(mockList).not.toHaveBeenCalled()
    vi.useRealTimers()
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`
Expected: FAIL（`Failed to resolve import "../KefuTakeoverPanel.vue"`）。

- [ ] **Step 3: `api/kefu.ts` 加会话接口**

在 `src/api/kefu.ts` 末尾追加：

```typescript
export function listSessions(params: { instance?: string; needs_human?: boolean; takeover?: boolean; status?: string }) {
  return get<{ sessions: any[] }>('/admin/kefu/sessions', params)
}
export function getSessionMessages(sid: string) { return get<{ messages: any[] }>(`/admin/kefu/sessions/${sid}/messages`) }
export function takeoverSession(sid: string) { return post<{ humanTakeover: boolean }>(`/admin/kefu/sessions/${sid}/takeover`, {}) }
export function releaseSession(sid: string) { return post<{ humanTakeover: boolean }>(`/admin/kefu/sessions/${sid}/release`, {}) }
export function humanReply(sid: string, content: string) { return post<{ messageId: string }>(`/admin/kefu/sessions/${sid}/messages`, { content }) }
```

（`get`/`post` 已在文件顶部 `import { get, post, patch, del } from '@/utils/request'`。）

- [ ] **Step 4: 写组件 `KefuTakeoverPanel.vue`**

`src/components/kefu/KefuTakeoverPanel.vue`:

```vue
<!-- src/components/kefu/KefuTakeoverPanel.vue -->
<template>
  <div class="ktp">
    <div class="ktp__queue">
      <div class="ktp__qbar">
        <el-radio-group v-model="filter" size="small">
          <el-radio-button label="all">全部</el-radio-button>
          <el-radio-button label="needs_human">待人工</el-radio-button>
          <el-radio-button label="takeover">接管中</el-radio-button>
        </el-radio-group>
        <el-button size="small" text @click="loadQueue">🔄</el-button>
      </div>
      <div v-if="!sessions.length" class="ktp__empty">暂无会话</div>
      <div v-else class="ktp__list">
        <div v-for="s in sessions" :key="s.id" class="ktp__item"
             :class="{ 'is-active': s.id === selectedSid }" @click="selectSession(s.id)">
          <span class="ktp__badge" :class="badgeClass(s)">{{ badgeText(s) }}</span>
          <div class="ktp__item-main">
            <div class="ktp__visitor">{{ shortId(s.visitor_id) }}</div>
            <div class="ktp__preview">{{ s.last_message || '（无消息）' }}</div>
          </div>
          <div class="ktp__item-meta">
            <span v-if="s.human_agent_id" class="ktp__agent">{{ s.human_agent_id }}</span>
            <span class="ktp__time">{{ hhmm(s.last_active_at) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="ktp__detail">
      <div v-if="!selectedSid" class="ktp__placeholder">从左侧选择一个会话</div>
      <template v-else>
        <div class="ktp__ops">
          <span class="ktp__ops-info">{{ selected?.human_takeover ? ('接管人：' + (selected?.human_agent_id || '')) : '未接管' }}</span>
          <el-button v-if="!selected?.human_takeover" type="primary" size="small" @click="takeover">接管</el-button>
          <el-button v-else size="small" @click="release">释放</el-button>
        </div>
        <div class="ktp__conv" ref="convEl">
          <div v-for="m in messages" :key="m.id" class="ktp__msg" :class="'ktp__msg--' + m.role">
            <div class="ktp__bubble">
              <span v-if="m.meta && m.meta.author === 'human'" class="ktp__human-tag">人工</span>
              {{ plainText(m.content) }}
            </div>
            <div class="ktp__msg-time">{{ hhmm(m.createdAt) }}</div>
          </div>
        </div>
        <div class="ktp__reply">
          <el-input v-model="replyDraft" :disabled="!selected?.human_takeover"
                    :placeholder="selected?.human_takeover ? '输入人工回复…' : '先接管再回复'"
                    @keydown.enter.prevent="sendReply" />
          <el-button type="primary" :disabled="!selected?.human_takeover || !replyDraft.trim()" @click="sendReply">发送</el-button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import * as api from '@/api/kefu'

const props = defineProps<{ instanceId: string }>()

const sessions = ref<any[]>([])
const filter = ref<'all' | 'needs_human' | 'takeover'>('all')
const selectedSid = ref('')
const messages = ref<any[]>([])
const replyDraft = ref('')
const convEl = ref<HTMLElement | null>(null)
let queueTimer: ReturnType<typeof setInterval> | null = null
let convTimer: ReturnType<typeof setInterval> | null = null

const selected = computed(() => sessions.value.find(s => s.id === selectedSid.value) || null)

function shortId(v: string) { return (v || '').slice(0, 8) }
function badgeText(s: any) { return s.needs_human ? '待人工' : s.human_takeover ? '接管中' : 'AI' }
function badgeClass(s: any) { return s.needs_human ? 'is-need' : s.human_takeover ? 'is-taken' : 'is-ai' }
function hhmm(ts: string | null) {
  if (!ts) return ''
  const d = new Date(ts); if (isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
function plainText(content: any) {
  const parts = Array.isArray(content) ? content : []
  return parts.filter((p: any) => p && p.type === 'text').map((p: any) => p.text).join('')
}

function queryParams() {
  const p: any = { instance: props.instanceId }
  if (filter.value === 'needs_human') p.needs_human = true
  else if (filter.value === 'takeover') p.takeover = true
  else p.status = 'active'
  return p
}

async function loadQueue() {
  if (!props.instanceId) { sessions.value = []; return }
  sessions.value = (await api.listSessions(queryParams())).sessions
}
async function loadConversation() {
  if (!selectedSid.value) return
  messages.value = (await api.getSessionMessages(selectedSid.value)).messages
  await nextTick(); if (convEl.value) convEl.value.scrollTop = convEl.value.scrollHeight
}
async function selectSession(sid: string) { selectedSid.value = sid; await loadConversation() }

async function takeover() {
  if (!selectedSid.value) return
  try { await api.takeoverSession(selectedSid.value); await loadQueue(); await loadConversation() }
  catch (e: any) { ElMessage.error(e?.response?.data?.error || '接管失败') }
}
async function release() {
  if (!selectedSid.value) return
  try { await api.releaseSession(selectedSid.value); await loadQueue(); await loadConversation() }
  catch (e: any) { ElMessage.error(e?.response?.data?.error || '释放失败') }
}
async function sendReply() {
  const text = replyDraft.value.trim()
  if (!text || !selected.value?.human_takeover) return
  try { await api.humanReply(selectedSid.value, text); replyDraft.value = ''; await loadConversation() }
  catch (e: any) { ElMessage.error(e?.response?.data?.error || '发送失败') }
}

watch(filter, () => { loadQueue() })
watch(() => props.instanceId, () => { selectedSid.value = ''; messages.value = []; loadQueue() })

onMounted(() => {
  loadQueue()
  queueTimer = setInterval(() => { loadQueue().catch(() => {}) }, 5000)
  convTimer = setInterval(() => { if (selectedSid.value) loadConversation().catch(() => {}) }, 5000)
})
onBeforeUnmount(() => {
  if (queueTimer) clearInterval(queueTimer)
  if (convTimer) clearInterval(convTimer)
})

defineExpose({ sessions, filter, selectedSid, messages, replyDraft, selected, selectSession, takeover, release, sendReply, loadQueue })
</script>

<style scoped>
.ktp { display: flex; height: 520px; border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 8px; overflow: hidden; }
.ktp__queue { width: 300px; border-right: 1px solid var(--el-border-color-lighter, #ebeef5); display: flex; flex-direction: column; }
.ktp__qbar { display: flex; align-items: center; justify-content: space-between; padding: 8px; border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5); }
.ktp__empty, .ktp__placeholder { padding: 40px 12px; text-align: center; color: var(--el-text-color-secondary, #909399); }
.ktp__list { flex: 1; overflow-y: auto; }
.ktp__item { display: flex; gap: 8px; padding: 10px; border-bottom: 1px solid var(--el-border-color-lighter, #f2f3f5); cursor: pointer; }
.ktp__item:hover { background: var(--el-fill-color-light, #f5f7fa); }
.ktp__item.is-active { background: var(--el-color-primary-light-9, #ecf5ff); }
.ktp__badge { flex-shrink: 0; font-size: 11px; padding: 1px 6px; border-radius: 8px; height: fit-content; }
.ktp__badge.is-need { background: #fff3e0; color: #e6913a; }
.ktp__badge.is-taken { background: #eef1fe; color: #4f6ef2; }
.ktp__badge.is-ai { background: var(--el-fill-color, #f0f2f5); color: var(--el-text-color-secondary, #909399); }
.ktp__item-main { flex: 1; min-width: 0; }
.ktp__visitor { font-size: 12px; color: var(--el-text-color-regular, #606266); }
.ktp__preview { font-size: 12px; color: var(--el-text-color-secondary, #909399); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ktp__item-meta { flex-shrink: 0; text-align: right; font-size: 11px; color: var(--el-text-color-secondary, #909399); }
.ktp__detail { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.ktp__ops { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5); }
.ktp__ops-info { font-size: 13px; color: var(--el-text-color-regular, #606266); }
.ktp__conv { flex: 1; overflow-y: auto; padding: 12px; }
.ktp__msg { display: flex; flex-direction: column; margin-bottom: 12px; }
.ktp__msg--user { align-items: flex-end; }
.ktp__bubble { max-width: 76%; padding: 8px 12px; border-radius: 10px; line-height: 1.5; word-break: break-word; }
.ktp__msg--assistant .ktp__bubble { background: var(--el-fill-color-light, #f4f4f5); color: var(--el-text-color-primary, #303133); }
.ktp__msg--user .ktp__bubble { background: var(--el-color-primary, #4f6ef2); color: #fff; }
.ktp__human-tag { display: inline-block; font-size: 10px; margin-right: 4px; padding: 0 4px; border-radius: 6px; background: #eef1fe; color: #4f6ef2; }
.ktp__msg-time { font-size: 11px; color: var(--el-text-color-secondary, #909399); margin-top: 2px; }
.ktp__reply { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--el-border-color-lighter, #ebeef5); }
</style>
```

- [ ] **Step 5: 运行确认通过**

Run: `npx vitest run src/components/kefu/__tests__/KefuTakeoverPanel.test.ts`
Expected: PASS（7 用例）。

- [ ] **Step 6: 类型检查**

Run: `npm run build`
Expected: vue-tsc 无错误。

- [ ] **Step 7: Commit**

```bash
git add src/api/kefu.ts src/components/kefu/KefuTakeoverPanel.vue src/components/kefu/__tests__/KefuTakeoverPanel.test.ts
git commit -m "feat(kefu): 管理端接管控制台组件 KefuTakeoverPanel + 会话接口"
```

---

### Task 2: 接入 `KefuManager.vue`「人工接管」tab

**Files:**
- Modify: `src/views/admin/KefuManager.vue`（`<el-tabs>` 加第 3 pane + import）
- Modify: `src/views/admin/__tests__/KefuManager.faq.test.ts`（stubs 补 `KefuTakeoverPanel`）

**Interfaces:**
- Consumes: Task 1 的 `KefuTakeoverPanel`（props `instanceId`）。

- [ ] **Step 1: 加 tab-pane + import**

在 `src/views/admin/KefuManager.vue` 的 `<el-tabs>` 里，「主页配置」`</el-tab-pane>`（第 70 行）之后、`</el-tabs>`（第 71 行）之前插入：

```html
      <el-tab-pane label="人工接管">
        <div class="km-tab-body">
          <KefuTakeoverPanel :instance-id="activeIid" />
        </div>
      </el-tab-pane>
```

在 `<script setup>` 的 import 段追加：

```typescript
import KefuTakeoverPanel from '@/components/kefu/KefuTakeoverPanel.vue'
```

- [ ] **Step 2: FAQ 测试补 stub**

在 `src/views/admin/__tests__/KefuManager.faq.test.ts` 的 `stubs` 对象追加：

```typescript
  KefuTakeoverPanel: { template: '<div />', props: ['instanceId'] },
```

- [ ] **Step 3: 运行 FAQ 测试 + 构建**

Run: `npx vitest run src/views/admin/__tests__/KefuManager.faq.test.ts`
Expected: PASS（6 用例，不受新 tab 影响）。

Run: `npm run build`
Expected: vue-tsc 无错误。

- [ ] **Step 4: Commit**

```bash
git add src/views/admin/KefuManager.vue src/views/admin/__tests__/KefuManager.faq.test.ts
git commit -m "feat(kefu): 客服设置页接入「人工接管」tab"
```

---

### Task 3: 文档 + Playwright 实机验证

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`（第 10 节补管理端控制台）
- Evidence: `.playwright-mcp/` 截图

- [ ] **Step 1: 确认服务在运行**

前端 `:5173`（`npm run dev`）、后端 `:3002`、OpenCode `:4096`。前端改动由 Vite HMR 生效。

- [ ] **Step 2: Playwright 走接管控制台闭环**

1. 先制造待人工会话：Playwright 打开 `http://localhost:5173/kefu/demo` → 点「🙋 转人工」（B1，置 needs_human）。
2. 另开/切到管理端（admin/admin123 登录）→ 设置中心 → 集成对接 → 智能客服 → 选「演示客服」→ 切「人工接管」tab：断言左侧队列出现该会话（徽标「待人工」）。
3. 点该会话 → 右侧显示对话 + 「接管」按钮 → 点接管 → 断言变「释放」+ 回复框启用（`disabled` 消失）。
4. 输入人工回复「您好，人工客服」发送 → 断言对话区出现该消息（带「人工」徽标）；回到访客页断言（≤3s 轮询）收到。
5. 点「释放」→ 断言回未接管态（「接管」按钮回来、回复框禁用）。
6. 截图存 `.playwright-mcp/kefu-takeover-console.png`。事后清理测试会话（`DELETE FROM ai_chat_sessions WHERE visitor_id='<测试id>'`）。

- [ ] **Step 3: 更新用户指南**

在 `docs/user-guide/ai/smart-customer-service.md` 第 10 节「人工接管」增补「管理端控制台（已交付）」小节：设置中心→集成对接→智能客服→「人工接管」tab；左队列（全部/待人工/接管中）、点会话看对话、接管/释放、接管中人工回复；5s 自动刷新；配一张截图。把管理端从「后续」改为「已交付」。

- [ ] **Step 4: Commit（文档）**

```bash
git add docs/user-guide/ai/smart-customer-service.md docs/user-guide/_images
git commit -m "docs(kefu): 人工接管管理端控制台说明 + 截图"
```

---

## Self-Review

**Spec coverage:**
- 5 个会话接口 → Task 1 Step 3 ✓
- `KefuTakeoverPanel`（队列/过滤/对话/接管/释放/回复/轮询/人工标记）→ Task 1 Step 4 ✓
- 队列限实例、过滤映射、`get` params → Task 1（`queryParams`）✓
- 回复仅接管中 → Task 1（`sendReply` + 模板 `:disabled`）✓
- 轮询 5s + 卸载清理 → Task 1（onMounted/onBeforeUnmount）✓
- 第 3 tab 接入 + FAQ 测试 stub → Task 2 ✓
- 文档 + Playwright → Task 3 ✓

**Placeholder scan:** 无 TBD/TODO；组件/测试为完整代码；测试含具体断言（含 fake timers）。✓

**Type consistency:**
- `listSessions(params)`/`getSessionMessages(sid)`/`takeoverSession(sid)`/`releaseSession(sid)`/`humanReply(sid,content)`（Task 1 Step 3 定义）与组件内 `api.*` 调用、测试 mock 名一致 ✓
- 组件 props `{ instanceId }` 与 Task 2 `:instance-id="activeIid"` 一致 ✓
- expose 的 `sessions/filter/selectedSid/messages/replyDraft/selected/selectSession/takeover/release/sendReply/loadQueue` 与测试断言一致 ✓
