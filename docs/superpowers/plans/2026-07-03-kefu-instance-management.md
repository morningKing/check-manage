# 客服实例管理（新建/编辑/删除）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在「设置中心 → 集成对接 → 智能客服」管理页补齐客服实例的新建/编辑/删除，并把 Agent/模型/系统提示词/欢迎语/品牌 logo 做成可视化表单。

**Architecture:** 纯前端。`api/kefu.ts` 补 `createInstance`/`deleteInstance` + 扩展类型；新增受控弹窗 `KefuInstanceDialog.vue`（新建/编辑共用，agent/模型下拉复用 `listAgents()`/`listModels()`）；`KefuManager.vue` 加入口与编排。后端 CRUD 已存在，不改。

**Tech Stack:** Vue 3 `<script setup>` + TS + Element Plus（全局 `<el-*>` 组件 + 仅 import `ElMessage`/`ElMessageBox`）+ Vitest + `@vue/test-utils`。

## Global Constraints

- **纯前端，不改后端**。后端接口现成：`POST/GET/PATCH/DELETE /admin/kefu/instances[/<iid>]`（均 `admin.kefu`）。
- slug 前端校验镜像后端正则：`^[a-z0-9][a-z0-9-]{0,63}$`；name 必填。二者不满足时保存禁用。后端为权威：400/409 用后端 `error` 文案经 `ElMessage.error` 显示。
- slug **创建时可填、编辑时只读**。
- Agent/模型下拉复用 `@/api/aiChat` 的 `listAgents()`（`:value="a.name"`）/`listModels()`（`:value="m.id"`，`m.id` 形如 `<providerID>/<modelID>`）；留空 → payload 对应字段为 `null`。
- 品牌 logo 存 `branding.logo`；保存时保留 `branding` 其它键。
- Element Plus 组件一律用**全局 kebab 组件**（`<el-dialog>` 等，不 import），只从 `element-plus` import `ElMessage`/`ElMessageBox`——与 `KefuManager.vue` 现有风格一致，且使现有 `KefuManager.faq.test.ts`（把 element-plus 整体 mock 成 `{ElMessage}` + 全局 stub el-*）不被破坏。
- 弹窗只管**实例级设置**；现有提示气泡/主页区块/FAQ 内联编辑器**不动**。
- 前端测试从 repo 根运行 `npx vitest run <file>`；构建 `npm run build`（vue-tsc）。提交只 `git add` 该任务确切文件，禁止 `git add -A`。

---

### Task 1: API 绑定 + `KefuInstanceDialog.vue`

**Files:**
- Modify: `src/api/kefu.ts`（扩展 `KefuInstanceFull` + 加 `createInstance`/`deleteInstance`）
- Create: `src/components/kefu/KefuInstanceDialog.vue`
- Create: `src/components/kefu/__tests__/KefuInstanceDialog.test.ts`

**Interfaces:**
- Produces: `createInstance(data)`、`deleteInstance(iid)`（`@/api/kefu`）；组件 `KefuInstanceDialog`，Props `{ modelValue: boolean; instance: KefuInstanceFull | null }`，Emits `update:modelValue(v)`、`saved(inst)`。

- [ ] **Step 1: 扩展 `src/api/kefu.ts`**

把现有 `KefuInstanceFull` 接口替换为（补字段），并在文件末尾加两个函数：

```ts
export interface KefuInstanceFull extends KefuInstance {
  agent?: string | null
  model?: string | null
  system_prompt?: string | null
  welcome_message?: string | null
  branding?: Record<string, any>
  guided_questions?: string[]
  panel_blocks?: any[]
}

export function createInstance(data: Partial<KefuInstanceFull> & { slug: string; name: string }) {
  return post<KefuInstance>('/admin/kefu/instances', data)
}
export function deleteInstance(iid: string) {
  return del(`/admin/kefu/instances/${iid}`)
}
```

（`get/post/patch/del` 已在文件顶部导入；`KefuInstance` 已含 `enabled`。）

- [ ] **Step 2: 写 `KefuInstanceDialog.test.ts`（失败测试）**

`src/components/kefu/__tests__/KefuInstanceDialog.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const mockCreate = vi.fn(); const mockUpdate = vi.fn()
vi.mock('@/api/kefu', () => ({
  createInstance: (...a: any[]) => mockCreate(...a),
  updateInstance: (...a: any[]) => mockUpdate(...a),
}))
vi.mock('@/api/aiChat', () => ({
  listAgents: () => Promise.resolve({ agents: [{ name: 'plan', description: '' }], subagents: [], default: null }),
  listModels: () => Promise.resolve({ models: [{ id: 'p/m', label: 'P / M', providerID: 'p', modelID: 'm', connected: true }], default: '', openCodeDefaults: {} }),
}))
vi.mock('element-plus', () => ({ ElMessage: { success: vi.fn(), error: vi.fn() } }))

import KefuInstanceDialog from '../KefuInstanceDialog.vue'

const stubs = {
  'el-dialog': { template: '<div><slot/><slot name="footer"/></div>', props: ['modelValue', 'title', 'width'] },
  'el-form': { template: '<form><slot/></form>', props: ['label-width'] },
  'el-form-item': { template: '<div><slot/></div>', props: ['label'] },
  'el-input': { props: ['modelValue', 'disabled', 'type', 'rows', 'placeholder'], template: `<input :disabled="disabled" :value="modelValue" @input="$emit('update:modelValue',$event.target.value)" />` },
  'el-switch': { props: ['modelValue'], template: `<input type="checkbox" :checked="modelValue" @change="$emit('update:modelValue',$event.target.checked)" />` },
  'el-select': { props: ['modelValue'], template: '<select><slot/></select>' },
  'el-option': { props: ['label', 'value'], template: '<option />' },
  'el-button': { props: ['type', 'disabled', 'loading'], template: `<button :disabled="disabled" @click="$emit('click')"><slot/></button>` },
}
const mountD = (instance: any = null) =>
  mount(KefuInstanceDialog, { props: { modelValue: true, instance }, global: { stubs } })

describe('KefuInstanceDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCreate.mockResolvedValue({ id: 'new1', slug: 'pw', name: 'PW', enabled: true })
    mockUpdate.mockResolvedValue({ id: 'i1', slug: 's', name: 'N', enabled: true })
  })

  it('create mode: slug editable, save calls createInstance with payload', async () => {
    const w = mountD(null)
    await flushPromises()
    expect(w.get('[data-test="slug"]').attributes('disabled')).toBeUndefined()
    ;(w.vm as any).slug = 'pw'; (w.vm as any).name = 'PW'
    await (w.vm as any).save()
    expect(mockCreate).toHaveBeenCalledWith({
      slug: 'pw', name: 'PW', enabled: true, welcome_message: '',
      agent: null, model: null, system_prompt: null, branding: { logo: '' },
    })
    expect(w.emitted('saved')).toBeTruthy()
  })

  it('edit mode: slug disabled + prefilled, updateInstance without slug, preserves branding', async () => {
    const inst = { id: 'i1', slug: 's', name: 'N', enabled: true, welcome_message: 'hi', agent: 'plan', model: 'p/m', system_prompt: 'sp', branding: { logo: 'L', theme: 'x' } }
    const w = mountD(inst)
    await flushPromises()
    expect(w.get('[data-test="slug"]').attributes('disabled')).toBeDefined()
    expect((w.vm as any).slug).toBe('s')
    expect((w.vm as any).agent).toBe('plan')
    await (w.vm as any).save()
    expect(mockUpdate).toHaveBeenCalledWith('i1', {
      name: 'N', enabled: true, welcome_message: 'hi', agent: 'plan', model: 'p/m',
      system_prompt: 'sp', branding: { logo: 'L', theme: 'x' },
    })
    expect(mockCreate).not.toHaveBeenCalled()
  })

  it('canSave: false without name or with invalid slug (create)', async () => {
    const w = mountD(null)
    await flushPromises()
    expect((w.vm as any).canSave).toBe(false)
    ;(w.vm as any).name = 'X'; (w.vm as any).slug = 'BAD!'
    await flushPromises()
    expect((w.vm as any).canSave).toBe(false)
    ;(w.vm as any).slug = 'ok-1'
    await flushPromises()
    expect((w.vm as any).canSave).toBe(true)
  })

  it('empty agent/model → null in payload', async () => {
    const w = mountD(null)
    await flushPromises()
    ;(w.vm as any).slug = 'pw'; (w.vm as any).name = 'PW'
    await (w.vm as any).save()
    const payload = mockCreate.mock.calls[0][0]
    expect(payload.agent).toBeNull()
    expect(payload.model).toBeNull()
  })
})
```

- [ ] **Step 3: 运行确认失败**

Run: `npx vitest run src/components/kefu/__tests__/KefuInstanceDialog.test.ts`
Expected: FAIL — `Failed to resolve import "../KefuInstanceDialog.vue"`.

- [ ] **Step 4: 写组件 `KefuInstanceDialog.vue`**

`src/components/kefu/KefuInstanceDialog.vue`:

```vue
<!-- src/components/kefu/KefuInstanceDialog.vue -->
<template>
  <el-dialog :model-value="modelValue" :title="instance ? '编辑客服' : '新建客服'" width="640px"
             @update:model-value="$emit('update:modelValue', $event)">
    <el-form label-width="96px">
      <el-form-item label="标识 slug">
        <el-input v-model="slug" data-test="slug" :disabled="!!instance" placeholder="小写字母/数字/连字符，如 presale" />
      </el-form-item>
      <el-form-item label="名称">
        <el-input v-model="name" placeholder="客服名称" />
      </el-form-item>
      <el-form-item label="启用">
        <el-switch v-model="enabled" />
      </el-form-item>
      <el-form-item label="欢迎语">
        <el-input v-model="welcome" type="textarea" :rows="2" placeholder="访客打开时的欢迎语" />
      </el-form-item>
      <el-form-item label="Agent">
        <el-select v-model="agent" clearable placeholder="使用 OpenCode 默认 Agent" style="width:100%">
          <el-option v-for="a in agents" :key="a.name" :label="a.name" :value="a.name" />
        </el-select>
      </el-form-item>
      <el-form-item label="模型">
        <el-select v-model="model" clearable filterable placeholder="使用默认模型" style="width:100%">
          <el-option v-for="m in models" :key="m.id" :label="m.label" :value="m.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="系统提示词">
        <el-input v-model="systemPrompt" type="textarea" :rows="4" placeholder="客服人设，会与固定护栏拼接" />
      </el-form-item>
      <el-form-item label="品牌 logo">
        <el-input v-model="logoUrl" placeholder="logo 图片 URL（可空）" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :disabled="!canSave" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import * as api from '@/api/kefu'
import type { KefuInstanceFull, KefuInstance } from '@/api/kefu'
import { listAgents, listModels } from '@/api/aiChat'
import type { AgentInfo, ModelInfo } from '@/api/aiChat'

const props = defineProps<{ modelValue: boolean; instance: KefuInstanceFull | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'saved', inst: KefuInstance): void }>()

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/
const slug = ref(''); const name = ref(''); const enabled = ref(true)
const welcome = ref(''); const agent = ref(''); const model = ref('')
const systemPrompt = ref(''); const logoUrl = ref(''); const saving = ref(false)
const agents = ref<AgentInfo[]>([]); const models = ref<ModelInfo[]>([])

const canSave = computed(() =>
  name.value.trim().length > 0 && (props.instance !== null || SLUG_RE.test(slug.value.trim())))

function reset() {
  const i = props.instance
  slug.value = i?.slug || ''
  name.value = i?.name || ''
  enabled.value = i ? i.enabled : true
  welcome.value = i?.welcome_message || ''
  agent.value = i?.agent || ''
  model.value = i?.model || ''
  systemPrompt.value = i?.system_prompt || ''
  logoUrl.value = i?.branding?.logo || ''
}
watch(() => props.modelValue, v => { if (v) reset() }, { immediate: true })

async function save() {
  if (!canSave.value) return
  saving.value = true
  const branding = { ...(props.instance?.branding || {}), logo: logoUrl.value.trim() }
  const base = {
    name: name.value.trim(), enabled: enabled.value, welcome_message: welcome.value,
    agent: agent.value || null, model: model.value || null,
    system_prompt: systemPrompt.value || null, branding,
  }
  try {
    const result = props.instance
      ? await api.updateInstance(props.instance.id, base)
      : await api.createInstance({ slug: slug.value.trim(), ...base })
    ElMessage.success(props.instance ? '已保存' : '客服已创建')
    emit('saved', result as KefuInstance)
    emit('update:modelValue', false)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.error || e?.message || '操作失败')
  } finally {
    saving.value = false
  }
}

async function loadOptions() {
  try { agents.value = (await listAgents()).agents } catch { /* non-fatal */ }
  try { models.value = (await listModels()).models } catch { /* non-fatal */ }
}
loadOptions()
</script>
```

- [ ] **Step 5: 运行确认通过**

Run: `npx vitest run src/components/kefu/__tests__/KefuInstanceDialog.test.ts`
Expected: PASS（4 用例）。

- [ ] **Step 6: 类型检查**

Run: `npm run build`
Expected: vue-tsc 无错误（确认新 API 绑定与组件类型正确）。

- [ ] **Step 7: Commit**

```bash
git add src/api/kefu.ts src/components/kefu/KefuInstanceDialog.vue src/components/kefu/__tests__/KefuInstanceDialog.test.ts
git commit -m "feat(kefu): 客服实例新建/编辑弹窗 KefuInstanceDialog + api createInstance/deleteInstance"
```

---

### Task 2: `KefuManager.vue` 接入新建/编辑/删除

**Files:**
- Modify: `src/views/admin/KefuManager.vue`
- Modify: `src/views/admin/__tests__/KefuManager.faq.test.ts`（stubs 加 `KefuInstanceDialog`）

**Interfaces:**
- Consumes: Task 1 的 `KefuInstanceDialog`（`v-model` + `:instance` + `@saved`）、`api.createInstance`/`deleteInstance`/`getInstance`/`updateInstance`。

- [ ] **Step 1: 模板加实例操作按钮 + 弹窗**

在 `src/views/admin/KefuManager.vue`，把第 4–7 行（select + 新增热问按钮）：

```html
    <el-select v-model="activeIid" placeholder="选择客服实例" @change="onInstanceChange" style="width:280px;margin:12px 0">
      <el-option v-for="i in instances" :key="i.id" :label="i.name" :value="i.id" />
    </el-select>
    <el-button type="primary" :disabled="!activeIid" @click="openCreate">新增热问</el-button>
```

替换为：

```html
    <el-select v-model="activeIid" placeholder="选择客服实例" @change="onInstanceChange" style="width:280px;margin:12px 0">
      <el-option v-for="i in instances" :key="i.id" :label="i.name" :value="i.id" />
    </el-select>
    <el-button type="primary" @click="openCreateInstance">+ 新建客服</el-button>
    <el-button v-if="activeIid" @click="openEditInstance">编辑客服</el-button>
    <el-button v-if="activeIid" type="danger" @click="removeInstance">删除客服</el-button>
    <el-button :disabled="!activeIid" @click="openCreate" style="margin-left:16px">新增热问</el-button>
```

在模板最外层 `</div>` 之前（第 66 行 `</div>` 之前）插入弹窗：

```html
    <KefuInstanceDialog v-model="showInstanceDialog" :instance="editingInstance" @saved="onInstanceSaved" />
```

- [ ] **Step 2: 脚本加实例编排逻辑**

在 `<script setup>` 的 import 段追加：

```typescript
import { ElMessage, ElMessageBox } from 'element-plus'
import KefuInstanceDialog from '@/components/kefu/KefuInstanceDialog.vue'
import type { KefuInstanceFull } from '@/api/kefu'
```

（若 `ElMessage` 已从 element-plus 导入，则把该行合并为同时导入 `ElMessage, ElMessageBox`，不要重复导入。）

在 `const blocks = ref<any[]>([])` 之后追加状态与函数：

```typescript
const showInstanceDialog = ref(false)
const editingInstance = ref<KefuInstanceFull | null>(null)

function openCreateInstance() { editingInstance.value = null; showInstanceDialog.value = true }
async function openEditInstance() {
  if (!activeIid.value) return
  editingInstance.value = await api.getInstance(activeIid.value)
  showInstanceDialog.value = true
}
async function removeInstance() {
  if (!activeIid.value) return
  try {
    await ElMessageBox.confirm('将同时删除该客服的所有热问，且不可恢复。确认删除？', '删除客服', { type: 'warning' })
  } catch { return }  // 用户取消
  await api.deleteInstance(activeIid.value)
  ElMessage.success('客服已删除')
  activeIid.value = ''
  faqs.value = []; bubbles.value = []; blocks.value = []
  await loadInstances()
}
async function onInstanceSaved(inst: { id: string }) {
  const creating = editingInstance.value === null
  await loadInstances()
  if (creating) { activeIid.value = inst.id; await onInstanceChange() }
}
```

- [ ] **Step 3: 现有 FAQ 测试加 KefuInstanceDialog stub**

在 `src/views/admin/__tests__/KefuManager.faq.test.ts` 的 `stubs` 对象里追加一项（与其它 stub 并列，避免真实子组件在挂载时触发 `listAgents`/`listModels` 网络请求）：

```typescript
  KefuInstanceDialog: { template: '<div />', props: ['modelValue', 'instance'] },
```

- [ ] **Step 4: 运行 FAQ 测试 + 弹窗测试 + 构建**

Run: `npx vitest run src/views/admin/__tests__/KefuManager.faq.test.ts src/components/kefu/__tests__/KefuInstanceDialog.test.ts`
Expected: PASS（FAQ 6 + 弹窗 4）。

Run: `npm run build`
Expected: vue-tsc 无错误。

- [ ] **Step 5: Commit**

```bash
git add src/views/admin/KefuManager.vue src/views/admin/__tests__/KefuManager.faq.test.ts
git commit -m "feat(kefu): 客服管理页接入实例新建/编辑/删除"
```

---

### Task 3: 文档同步 + Playwright 实机验证

**Files:**
- Modify: `docs/user-guide/ai/smart-customer-service.md`（§9.2 新增实例增删改说明）
- Evidence: `.playwright-mcp/` 截图

- [ ] **Step 1: 确认服务在运行**

前端 `:5173`、后端 `:3002`、MCP `:3003`、**OpenCode `:4096`**（访客页会话依赖；`opencode serve --port 4096 --hostname 127.0.0.1` 若未运行）。前端改动由 Vite HMR 生效。

- [ ] **Step 2: Playwright 走增删改流程**

Playwright（admin/admin123 登录），访问 `http://localhost:5173/admin/integration?tab=kefu`：
1. 点「+ 新建客服」→ 弹窗填 slug `pw-test`、名称「验证客服」、欢迎语、选一个模型 → 保存 → 断言下拉出现并自动选中「验证客服」。
2. 新标签打开 `http://localhost:5173/kefu/pw-test` → 断言页面正常加载、欢迎语正确。
3. 回管理页点「编辑客服」→ 断言 slug 输入框只读、字段预填 → 改系统提示词 → 保存成功。
4. 点「删除客服」→ 确认框确认 → 断言下拉不再有「验证客服」。
5. 截图存 `.playwright-mcp/kefu-instance-mgmt.png`。

- [ ] **Step 3: 更新用户指南**

在 `docs/user-guide/ai/smart-customer-service.md` §9.2 增加「9.2.0 新建/编辑/删除客服实例」小节：入口（设置中心→集成对接→智能客服，下拉旁按钮）、表单字段（slug 创建后不可改、名称、启用、欢迎语、**Agent、模型、系统提示词**、品牌 logo）、删除级联热问的提示。

- [ ] **Step 4: Commit（文档）**

```bash
git add docs/user-guide/ai/smart-customer-service.md
git commit -m "docs(kefu): 管理页新建/编辑/删除客服实例说明"
```

---

## Self-Review

**Spec coverage:**
- API `createInstance`/`deleteInstance` + 类型扩展 → Task 1 Step 1 ✓
- 弹窗字段（slug 编辑只读 / 名称 / 启用 / 欢迎语 / Agent / 模型 / 系统提示词 / logo）→ Task 1 Step 4 ✓
- slug 校验镜像正则 + name 必填 + 400/409 提示 → Task 1（`SLUG_RE`/`canSave`/catch）✓
- agent/模型复用 listAgents/listModels + 空→null → Task 1 ✓
- branding.logo 映射 + 保留其它键 → Task 1（`{ ...branding, logo }`）+ 测试 ✓
- KefuManager 新建/编辑/删除入口 + 确认 + 刷新/选中 → Task 2 ✓
- 现有内联编辑器不动 → Task 2 只加按钮/弹窗，未改 FAQ/home 逻辑 ✓
- 现有 FAQ 测试保持绿（加 stub）→ Task 2 Step 3 ✓
- 文档 + Playwright → Task 3 ✓

**Placeholder scan:** 无 TBD/TODO；每个 code step 含完整代码；测试含具体断言。✓

**Type consistency:**
- `KefuInstanceDialog` Props `{ modelValue, instance }` / Emits `update:modelValue`/`saved` 在 Task 1 定义，Task 2 以 `v-model`/`:instance`/`@saved` 一致绑定 ✓
- `createInstance(data)`/`deleteInstance(iid)` 在 Task 1 定义，Task 2 `api.createInstance`（弹窗内）/`api.deleteInstance`（KefuManager）一致调用 ✓
- `onInstanceSaved(inst)` 用 `inst.id`，与 `saved` emit 的 `KefuInstance`（含 id）一致 ✓
- slug 正则 `^[a-z0-9][a-z0-9-]{0,63}$` 与后端一致 ✓
