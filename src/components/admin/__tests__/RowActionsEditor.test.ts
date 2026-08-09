/**
 * RowActionsEditor 组件单元测试
 *
 * 覆盖：空态展示、新增/删除动作的 v-model 回传、字段候选来源。
 * 深层交互（条件构造器、参数表单、响应映射的具体输入）留给 Playwright 实跑
 * 覆盖（Task 10），这里聚焦组件对外契约（props/emit/expose）。
 */
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RowActionsEditor from '../RowActionsEditor.vue'
import type { RowActionConfig } from '@/types/rowAction'

beforeAll(() => {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as any
})

// 真实签名：getWebhookRules() 直接返回 WebhookRule[]（不是 { rules: [...] }）
vi.mock('@/api/webhook', () => ({
  getWebhookRules: vi.fn().mockResolvedValue([
    { id: 'wh-1', name: '推送外部', triggerEvent: 'manual', enabled: true },
    { id: 'wh-2', name: '创建时同步', triggerEvent: 'create', enabled: true },
  ]),
}))
// 真实导出名是 getScanTasks（不是 listScanTasks），直接返回 AiScanTask[]
vi.mock('@/api/aiScanTask', () => ({
  getScanTasks: vi.fn().mockResolvedValue([{ id: 'st-1', name: '自动审核' }]),
}))
// 角色候选走 useRoleStore -> getRoleOptions()；mock 掉避免测试环境发真实请求
vi.mock('@/api/role', () => ({
  getRoleOptions: vi.fn().mockResolvedValue([{ id: 'developer', name: '开发者', isSystem: true, isSuperuser: false }]),
  getRoles: vi.fn(),
  getRole: vi.fn(),
  getPermissionCatalog: vi.fn(),
  createRole: vi.fn(),
  updateRole: vi.fn(),
  deleteRole: vi.fn(),
  updateRoleMenuVisibility: vi.fn(),
}))

const FIELDS = [
  { fieldName: 'status', label: '状态', controlType: 'select' },
  { fieldName: 'result', label: '结果', controlType: 'text' },
] as any

// Element Plus 组件的最小 stub（测试环境未全局安装 Element Plus，见 CLAUDE.md 测试模式约定）
const stubs = {
  'el-input': {
    template: `<input :value="modelValue" @input="$emit('update:modelValue', $event.target.value)" />`,
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-select': {
    template: `<select :value="modelValue" @change="$emit('update:modelValue', $event.target.value)"><slot /></select>`,
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-option': { template: '<option :value="value">{{ label }}</option>', props: ['value', 'label'] },
  'el-button': { template: '<button @click="$emit(\'click\')"><slot /></button>', emits: ['click'] },
  'el-checkbox': {
    template: `<input type="checkbox" :checked="modelValue" @change="$emit('update:modelValue', $event.target.checked)" />`,
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-switch': {
    template: `<input type="checkbox" :checked="modelValue" @change="$emit('update:modelValue', $event.target.checked)" />`,
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-radio-group': { template: '<div><slot /></div>', props: ['modelValue'], emits: ['update:modelValue'] },
  'el-radio': { template: '<label><slot /></label>', props: ['label'] },
  'el-form': { template: '<form><slot /></form>' },
  'el-form-item': { template: '<div><slot /></div>', props: ['label', 'required'] },
  'el-divider': { template: '<div><slot /></div>', props: ['contentPosition'] },
  'el-tag': { template: '<span><slot /></span>', props: ['type', 'size'] },
  'el-empty': { template: '<div class="empty">{{ description }}</div>', props: ['description', 'imageSize'] },
  'el-alert': { template: '<div class="alert">{{ title }}</div>', props: ['title', 'type', 'closable', 'showIcon'] },
}

function mountEditor(modelValue: RowActionConfig[] = []) {
  setActivePinia(createPinia())
  return mount(RowActionsEditor, {
    props: { modelValue, fields: FIELDS },
    global: { stubs },
  })
}

describe('RowActionsEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('空配置时展示空态', async () => {
    const w = mountEditor()
    await flushPromises()
    expect(w.text()).toContain('尚未配置行操作')
  })

  it('新增动作会 emit 一条带 id 的默认配置', async () => {
    const w = mountEditor()
    await flushPromises()
    ;(w.vm as any).addAction()
    await flushPromises()
    const emitted = w.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    const list = emitted![emitted!.length - 1][0] as RowActionConfig[]
    expect(list).toHaveLength(1)
    expect(list[0].id).toBeTruthy()
    expect(list[0].actionType).toBe('webhook')
    expect(list[0].enabled).toBe(true)
  })

  it('删除动作会从列表移除', async () => {
    const existing: RowActionConfig[] = [
      { id: 'ra-1', label: 'A', actionType: 'webhook', enabled: true },
      { id: 'ra-2', label: 'B', actionType: 'aiTask', enabled: true },
    ]
    const w = mountEditor(existing)
    await flushPromises()
    ;(w.vm as any).removeAction(0)
    await flushPromises()
    const emitted = w.emitted('update:modelValue')!
    const list = emitted[emitted.length - 1][0] as RowActionConfig[]
    expect(list.map((a) => a.id)).toEqual(['ra-2'])
  })

  it('只把本页面的字段作为状态字段候选', async () => {
    const w = mountEditor([
      { id: 'ra-1', label: 'A', actionType: 'webhook', enabled: true },
    ])
    await flushPromises()
    expect((w.vm as any).fieldOptions.map((o: any) => o.value)).toEqual([
      'status',
      'result',
    ])
  })

  it('只列出手动触发的 Webhook 规则', async () => {
    const w = mountEditor([
      { id: 'ra-1', label: 'A', actionType: 'webhook', enabled: true },
    ])
    await flushPromises()
    // getWebhookRules mock 返回一条 manual + 一条 create；组件应过滤掉非 manual 的
    expect(w.html()).toContain('推送外部')
    expect(w.html()).not.toContain('创建时同步')
  })

  it('切换到 aiTask 类型会清空 webhook 专属的执行结果字段', async () => {
    const w = mountEditor([
      {
        id: 'ra-1',
        label: 'A',
        actionType: 'webhook',
        enabled: true,
        statusField: 'status',
        runningValue: '执行中',
        doneValue: '已完成',
        failedValue: '失败',
        responseMapping: [{ jsonKey: 'ok', column: 'result', required: false }],
      },
    ])
    await flushPromises()
    const vm = w.vm as any
    vm.current.actionType = 'aiTask'
    await flushPromises()
    expect(vm.current.statusField).toBeUndefined()
    expect(vm.current.runningValue).toBeUndefined()
    expect(vm.current.doneValue).toBeUndefined()
    expect(vm.current.failedValue).toBeUndefined()
    expect(vm.current.responseMapping).toBeUndefined()
  })

  it('单纯切换选中的另一个动作不会清空数据（只有真正切换类型才清空）', async () => {
    const w = mountEditor([
      { id: 'ra-1', label: 'A', actionType: 'webhook', enabled: true, statusField: 'status' },
      { id: 'ra-2', label: 'B', actionType: 'aiTask', enabled: true, scanTaskId: 'st-1' },
    ])
    await flushPromises()
    const vm = w.vm as any
    vm.activeIndex = 1
    await flushPromises()
    vm.activeIndex = 0
    await flushPromises()
    expect(vm.list[0].statusField).toBe('status')
  })
})
