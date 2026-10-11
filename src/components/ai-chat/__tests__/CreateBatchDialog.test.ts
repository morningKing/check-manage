import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import CreateBatchDialog from '../CreateBatchDialog.vue'
import * as batchApi from '@/api/aiChatBatches'
import * as tplApi from '@/api/aiChatPromptTemplates'
import * as chatApi from '@/api/aiChat'
import * as policyApi from '@/api/gatePolicies'

vi.mock('@/api/aiChatBatches')
vi.mock('@/api/aiChatPromptTemplates')
vi.mock('@/api/aiChat')
vi.mock('@/api/gatePolicies')

beforeAll(() => {
  globalThis.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} } as any
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(tplApi.listTemplates).mockResolvedValue([])
  vi.mocked(chatApi.listAgents).mockResolvedValue({ agents: [], subagents: [], default: null })
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

describe('CreateBatchDialog', () => {
  it('disables 创建 until name + prompt + ≥1 staged file', async () => {
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    const create = w.find('button[data-test="create-btn"]')
    expect((create.element as HTMLButtonElement).disabled).toBe(true)
    // simulate name + prompt
    await w.find('input[data-test="name"]').setValue('B1')
    await w.find('input[data-test="prompt"]').setValue('do')
    expect((create.element as HTMLButtonElement).disabled).toBe(true)
    // simulate one staged file present
    ;(w.vm as any).stagedFiles = [{ name: 'a.txt', path: 'batch-staging/x/y/a.txt' }]
    await flushPromises()
    expect((w.find('button[data-test="create-btn"]').element as HTMLButtonElement).disabled).toBe(false)
  })

  it('calls createBatch with the staged file list on submit', async () => {
    vi.mocked(batchApi.createBatch).mockResolvedValue({
      batch: { id: 'b', user_id: 'u', name: 'B1', prompt: 'do',
               template_id: null, agent: null, model: null, status: 'pending', total: 1,
               done: 0, failed: 0, created_at: '', completed_at: null },
      sessions: [],
    })
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B1')
    await w.find('input[data-test="prompt"]').setValue('do')
    ;(w.vm as any).stagedFiles = [{ name: 'a.txt', path: 'p/a.txt' }]
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(batchApi.createBatch).toHaveBeenCalledWith(expect.objectContaining({
      name: 'B1', prompt: 'do',
      files: [{ name: 'a.txt', path: 'p/a.txt' }],
    }))
  })

  it('saves a new template when 保存为模板 is checked', async () => {
    vi.mocked(batchApi.createBatch).mockResolvedValue({
      batch: { id: 'b', user_id: 'u', name: 'B', prompt: 'p',
               template_id: null, agent: null, model: null, status: 'pending', total: 1,
               done: 0, failed: 0, created_at: '', completed_at: null },
      sessions: [],
    })
    vi.mocked(tplApi.createTemplate).mockResolvedValue({} as any)
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B')
    await w.find('input[data-test="prompt"]').setValue('hello')
    ;(w.vm as any).stagedFiles = [{ name: 'a', path: 'p' }]
    ;(w.vm as any).saveAsTemplate = true
    ;(w.vm as any).templateName = 'My T'
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(tplApi.createTemplate).toHaveBeenCalledWith('My T', 'hello')
  })

  it('sends action_checks when 动作门禁 is enabled with a complete check', async () => {
    vi.mocked(batchApi.createBatch).mockResolvedValue({
      batch: { id: 'b', user_id: 'u', name: 'B', prompt: 'p',
               template_id: null, agent: null, model: null, status: 'pending', total: 1,
               done: 0, failed: 0, created_at: '', completed_at: null },
      sessions: [],
    })
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B')
    await w.find('input[data-test="prompt"]').setValue('p')
    ;(w.vm as any).stagedFiles = [{ name: 'a', path: 'p' }]
    ;(w.vm as any).gateEnabled = true
    ;(w.vm as any).gateChecks = [
      { name: '克隆目标仓库', tool: 'bash', args_pattern: 'git clone\\s+\\S*acme/inspector', min_count: 1 },
      { name: '', tool: 'bash', args_pattern: '', min_count: 1 },  // 不完整的行被过滤
    ]
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(batchApi.createBatch).toHaveBeenCalledWith(expect.objectContaining({
      action_checks: [
        { name: '克隆目标仓库', check_type: 'tool', tool: 'bash',
          args_pattern: 'git clone\\s+\\S*acme/inspector', min_count: 1 },
      ],
    }))
  })

  it('verifier 行提交 {name, check_type, rubric} 且不带 tool/args_pattern/min_count', async () => {
    vi.mocked(batchApi.createBatch).mockResolvedValue({
      batch: { id: 'b', user_id: 'u', name: 'B', prompt: 'p',
               template_id: null, agent: null, model: null, status: 'pending', total: 1,
               done: 0, failed: 0, created_at: '', completed_at: null },
      sessions: [],
    })
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B')
    await w.find('input[data-test="prompt"]').setValue('p')
    ;(w.vm as any).stagedFiles = [{ name: 'a', path: 'p' }]
    ;(w.vm as any).gateEnabled = true
    ;(w.vm as any).gateChecks = [
      { name: '结论含标记', check_type: 'verifier', tool: 'bash',
        args_pattern: '', min_count: 1, subagents: '', rubric: '回复包含 MARK' },
    ]
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(batchApi.createBatch).toHaveBeenCalledWith(expect.objectContaining({
      action_checks: [{ name: '结论含标记', check_type: 'verifier', rubric: '回复包含 MARK' }],
    }))
  })

  it('pre(拦截)行提交保留 mode:pre——deny 规则不被静默降级为 post', async () => {
    vi.mocked(batchApi.createBatch).mockResolvedValue({
      batch: { id: 'b', user_id: 'u', name: 'B', prompt: 'p',
               template_id: null, agent: null, model: null, status: 'pending', total: 1,
               done: 0, failed: 0, created_at: '', completed_at: null },
      sessions: [],
    })
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B')
    await w.find('input[data-test="prompt"]').setValue('p')
    ;(w.vm as any).stagedFiles = [{ name: 'a', path: 'p' }]
    ;(w.vm as any).gateEnabled = true
    ;(w.vm as any).gateChecks = [
      { name: '禁删产物', check_type: 'tool', tool: 'bash',
        args_pattern: 'rm\\s+-rf', min_count: 1, subagents: '', rubric: '',
        mode: 'pre', timing: 'before', prompt: '', max_checks: 1,
        hasAssert: false, assert_tool: '', assert_pattern: '', assert_min_count: 1 },
    ]
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(batchApi.createBatch).toHaveBeenCalledWith(expect.objectContaining({
      action_checks: [
        { name: '禁删产物', mode: 'pre', check_type: 'tool', tool: 'bash',
          args_pattern: 'rm\\s+-rf', min_count: 1 },
      ],
    }))
  })

  it('pre 行 raw 透传:apply_to 等编辑面未建模的键原样保留,建模键以编辑值为准', async () => {
    vi.mocked(batchApi.createBatch).mockResolvedValue({
      batch: { id: 'b', user_id: 'u', name: 'B', prompt: 'p',
               template_id: null, agent: null, model: null, status: 'pending', total: 1,
               done: 0, failed: 0, created_at: '', completed_at: null },
      sessions: [],
    })
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B')
    await w.find('input[data-test="prompt"]').setValue('p')
    ;(w.vm as any).stagedFiles = [{ name: 'a', path: 'p' }]
    ;(w.vm as any).gateEnabled = true
    ;(w.vm as any).gateChecks = [
      { name: '禁删产物', check_type: 'tool', tool: 'bash',
        args_pattern: 'rm\\s+-rf', min_count: 2, subagents: 'general', rubric: '',
        mode: 'pre', timing: 'before', prompt: '', max_checks: 1,
        hasAssert: false, assert_tool: '', assert_pattern: '', assert_min_count: 1,
        // 回填时留存的原始行:raw 上的未建模键透传,UI 建模键(min_count)以编辑值为准
        raw: { name: '禁删产物', mode: 'pre', check_type: 'tool', tool: 'bash',
               args_pattern: 'rm\\s+-rf', min_count: 99,
               apply_to: { batch_seq: [1] }, require_state: 'clean' } },
    ]
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(batchApi.createBatch).toHaveBeenCalledWith(expect.objectContaining({
      action_checks: [
        { name: '禁删产物', mode: 'pre', check_type: 'tool', tool: 'bash',
          args_pattern: 'rm\\s+-rf', min_count: 2, subagents: ['general'],
          apply_to: { batch_seq: [1] }, require_state: 'clean' },
      ],
    }))
  })

  it('blocks submit when 动作门禁 is enabled but no complete check', async () => {
    vi.mocked(batchApi.createBatch).mockClear()
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B')
    await w.find('input[data-test="prompt"]').setValue('p')
    ;(w.vm as any).stagedFiles = [{ name: 'a', path: 'p' }]
    ;(w.vm as any).gateEnabled = true
    ;(w.vm as any).gateChecks = [{ name: '', tool: 'bash', args_pattern: '', min_count: 1 }]
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(batchApi.createBatch).not.toHaveBeenCalled()
  })

  // policy-only 组合(spec §8.2):后端支持「策略 ∪ 空批 checks」,UI 主路径放行
  it('allows submit with gate policy selected but no complete check (payload action_checks null + gatePolicyId)', async () => {
    vi.mocked(batchApi.createBatch).mockResolvedValue({
      batch: { id: 'b', user_id: 'u', name: 'B', prompt: 'p',
               template_id: null, agent: null, model: null, status: 'pending', total: 1,
               done: 0, failed: 0, created_at: '', completed_at: null },
      sessions: [],
    })
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B')
    await w.find('input[data-test="prompt"]').setValue('p')
    ;(w.vm as any).stagedFiles = [{ name: 'a', path: 'p' }]
    ;(w.vm as any).gateEnabled = true
    ;(w.vm as any).gateChecks = [{ name: '', tool: 'bash', args_pattern: '', min_count: 1 }]  // 全是不完整行 → 无有效规则
    ;(w.vm as any).gatePolicyId = 'pol-1'
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    // 空规则数组归一为 null(键显式存在),策略引用随请求下发
    expect(batchApi.createBatch).toHaveBeenCalledWith(expect.objectContaining({
      action_checks: null,
      gatePolicyId: 'pol-1',
    }))
  })

  it('still blocks submit with no complete check and no gate policy selected', async () => {
    vi.mocked(batchApi.createBatch).mockClear()
    const w = mount(CreateBatchDialog, {
      props: { modelValue: true },
      global: { stubs },
    })
    await w.find('input[data-test="name"]').setValue('B')
    await w.find('input[data-test="prompt"]').setValue('p')
    ;(w.vm as any).stagedFiles = [{ name: 'a', path: 'p' }]
    ;(w.vm as any).gateEnabled = true
    ;(w.vm as any).gateChecks = [{ name: '', tool: 'bash', args_pattern: '', min_count: 1 }]
    ;(w.vm as any).gatePolicyId = ''
    await flushPromises()
    await w.find('button[data-test="create-btn"]').trigger('click')
    await flushPromises()
    expect(batchApi.createBatch).not.toHaveBeenCalled()
  })
})
