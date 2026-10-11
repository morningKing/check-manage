import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import EditBatchConfigDialog from '../EditBatchConfigDialog.vue'
import * as chatApi from '@/api/aiChat'
import * as policyApi from '@/api/gatePolicies'

// store 用具名 mock:updateBatchConfig 收集保存 body,断言提交契约
const { updateBatchConfigMock } = vi.hoisted(() => ({ updateBatchConfigMock: vi.fn() }))
vi.mock('@/stores/aiChatBatches', () => ({
  useAiChatBatchesStore: () => ({ updateBatchConfig: updateBatchConfigMock }),
}))
vi.mock('@/api/aiChat')
vi.mock('@/api/gatePolicies')

beforeAll(() => {
  globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(chatApi.listAgents).mockResolvedValue({ agents: [], subagents: [], default: null })
  // prefill→loadPolicies 走 .then/.catch 链,auto-mock 返回 undefined 会在
  // .then 处同步抛错,必须显式 mockResolvedValue
  vi.mocked(policyApi.listGatePolicies).mockResolvedValue({ policies: [] })
})

const stubs = {
  'el-dialog': { template: '<div><slot /><slot name="footer" /></div>' },
  'el-input':  {
    template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-select': {
    template: '<select><slot /></select>',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-option':   { template: '<option />' },
  'el-checkbox': {
    template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-button': { template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>', props: ['disabled'], emits: ['click'] },
  'el-input-number': true,
  'el-upload': true,
}

// 批上已有 pre(拦截)行(API/模板直写,mode 键真实存在)+ 一条普通 post 行
const baseBatch = {
  id: 'b1', user_id: 'u', name: 'B1', prompt: 'p', template_id: null,
  agent: null, model: null, status: 'pending', total: 1, done: 0, failed: 0,
  created_at: '', completed_at: null, gate_retry: false,
  action_checks: [
    { name: '禁删产物', mode: 'pre', check_type: 'tool', tool: 'bash',
      args_pattern: 'rm\\s+-rf', min_count: 1,
      apply_to: { input_file_glob: 'docs/**' } },
    { name: '克隆目标仓库', check_type: 'tool', tool: 'bash',
      args_pattern: 'git clone', min_count: 1 },
  ],
} as any

async function mountAndSave(batch: Record<string, unknown> = baseBatch) {
  const w = mount(EditBatchConfigDialog, {
    props: { modelValue: true, batch: batch as any },
    global: { stubs },
  })
  await flushPromises()
  const save = w.findAll('button').find(b => b.text().includes('保存'))!
  await save.trigger('click')
  await flushPromises()
  expect(updateBatchConfigMock).toHaveBeenCalledTimes(1)
  return updateBatchConfigMock.mock.calls[0][1] as { action_checks: any[] }
}

describe('EditBatchConfigDialog pre 规则行 round-trip', () => {
  it('回填保留 mode:pre,保存时 deny 行原样透传(apply_to 不丢,post 行契约不变)', async () => {
    const body = await mountAndSave()
    expect(body.action_checks).toEqual([
      // pre 行:mode 键原样保留 + raw 上的未建模键透传——不降级成 post
      { name: '禁删产物', mode: 'pre', check_type: 'tool', tool: 'bash',
        args_pattern: 'rm\\s+-rf', min_count: 1,
        apply_to: { input_file_glob: 'docs/**' } },
      // post 行:既有提交契约不变(无 mode 键)
      { name: '克隆目标仓库', tool: 'bash', args_pattern: 'git clone', min_count: 1 },
    ])
  })

  it('pre 行编辑后保存:编辑值生效,mode:pre 与 apply_to 仍保留', async () => {
    const w = mount(EditBatchConfigDialog, {
      props: { modelValue: true, batch: baseBatch },
      global: { stubs },
    })
    await flushPromises()
    ;(w.vm as any).gateChecks[0].name = '禁删产物 v2'
    ;(w.vm as any).gateChecks[0].min_count = 3
    await flushPromises()
    const save = w.findAll('button').find(b => b.text().includes('保存'))!
    await save.trigger('click')
    await flushPromises()
    expect(updateBatchConfigMock).toHaveBeenCalledTimes(1)
    const body = updateBatchConfigMock.mock.calls[0][1] as { action_checks: any[] }
    expect(body.action_checks[0]).toEqual({
      name: '禁删产物 v2', mode: 'pre', check_type: 'tool', tool: 'bash',
      args_pattern: 'rm\\s+-rf', min_count: 3,
      apply_to: { input_file_glob: 'docs/**' },
    })
  })
})
