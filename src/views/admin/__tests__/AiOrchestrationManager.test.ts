/**
 * AiOrchestrationManager 组件单测（P3-A7）
 *
 * 覆盖：
 * - 挂载时并行拉取定义与运行列表（definitions / runs 集合键）
 * - 发布对话框：JSON 解析失败 / 缺 name / nodes 为空 → 拦截且不发请求
 * - 发布成功 → publishDefinition 收到解析后的 body，成功提示 + 刷新定义列表
 * - 运行展开行懒加载 steps：首次展开调 getRun，重复展开不重复请求
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

// Mock ElMessage before component import
vi.mock('element-plus', () => ({
  ElMessage: { warning: vi.fn(), success: vi.fn(), error: vi.fn() },
}))

// Mock auth store：当前用户具备编排管理能力
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    can: (key: string) => key === 'admin.ai_orchestration_admin',
  }),
}))

// Mock the API layer
const mockListDefinitions = vi.fn()
const mockPublishDefinition = vi.fn()
const mockListRuns = vi.fn()
const mockGetRun = vi.fn()

vi.mock('@/api/orchestration', () => ({
  listDefinitions: (...args: any[]) => mockListDefinitions(...args),
  publishDefinition: (...args: any[]) => mockPublishDefinition(...args),
  listRuns: (...args: any[]) => mockListRuns(...args),
  getRun: (...args: any[]) => mockGetRun(...args),
}))

import AiOrchestrationManager from '../AiOrchestrationManager.vue'
import { ElMessage } from 'element-plus'

// el-table stub：给运行表一个触发 expand-change 的按钮（定义表不受影响）
const ElTableStub = {
  template: `<div>
    <button class="stub-trigger-expand" @click="$emit('expand-change', data[0], [data[0]])" />
  </div>`,
  props: ['data', 'rowKey'],
  emits: ['expand-change'],
}

const stubs = {
  'el-tabs': { template: '<div><slot /></div>', props: ['modelValue'] },
  'el-tab-pane': { template: '<div><slot /></div>', props: ['label', 'name'] },
  // 渲染 data 条数即可断言绑定；不渲染列 scoped slot（拿不到 row）
  'el-table': ElTableStub,
  'el-table-column': { template: '<div />' },
  'el-button': {
    template: '<button @click="$emit(\'click\')"><slot /></button>',
    props: ['type', 'plain', 'loading'],
    emits: ['click'],
  },
  'el-dialog': {
    template: '<div class="stub-dialog"><slot /><slot name="footer" /></div>',
    props: ['modelValue', 'title', 'width', 'closeOnClickModal'],
    emits: ['update:modelValue'],
  },
  'el-input': {
    template: `<textarea :value="modelValue"
      @input="$emit('update:modelValue', $event.target.value)" />`,
    props: ['modelValue', 'type', 'rows', 'placeholder'],
    emits: ['update:modelValue'],
  },
  'el-tag': { template: '<span><slot /></span>', props: ['size', 'type'] },
  'el-alert': { template: '<div><slot /></div>', props: ['title', 'type', 'showIcon', 'closable'] },
}

const sampleDefinitions = [
  { id: 'orch_a', version: 2, name: '编排A', description: '两步', publishedAt: '2026-10-01T10:00:00' },
]
const sampleRuns = [
  { id: 'run_1', definition_id: 'orch_a', definition_version: 2, status: 'running',
    requested_by: 'alice', error_code: null, createdAt: '2026-10-02T09:00:00' },
]
const sampleSteps = [
  { id: 'run_1:step-1', node_id: 'step-1', kind: 'agent', name: '第一步', status: 'succeeded',
    depends_on: [], session_id: null, attempt_count: 1, output: null,
    error_message: null, started_at: null, finished_at: null },
]

async function mountView() {
  const wrapper = mount(AiOrchestrationManager, {
    global: {
      stubs,
      directives: { loading: { /* jsdom 无需真实指令 */ } },
    },
  })
  await nextTick()
  return wrapper
}

/** 两个 tab 各有一张 el-table；定义表在前，运行表在后 */
function expandButtons(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll('.stub-trigger-expand')
}

describe('AiOrchestrationManager', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListDefinitions.mockResolvedValue({ definitions: sampleDefinitions })
    mockListRuns.mockResolvedValue({ runs: sampleRuns })
    mockGetRun.mockResolvedValue({ ...sampleRuns[0], steps: sampleSteps })
    mockPublishDefinition.mockResolvedValue({ id: 'orch_a', version: 3 })
  })

  it('挂载时并行拉取定义与运行列表', async () => {
    const wrapper = await mountView()
    expect(mockListDefinitions).toHaveBeenCalledTimes(1)
    expect(mockListRuns).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('发布校验：JSON 解析失败不发请求', async () => {
    const wrapper = await mountView()
    // 打开对话框
    await wrapper.findAll('button').find(b => b.text() === '发布定义')!.trigger('click')
    const textarea = wrapper.find('textarea')
    await textarea.setValue('{not json')
    await wrapper.findAll('button').find(b => b.text() === '发布')!.trigger('click')
    expect(ElMessage.warning).toHaveBeenCalled()
    expect(mockPublishDefinition).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('发布校验：缺 name / nodes 为空都拦截', async () => {
    const wrapper = await mountView()
    await wrapper.findAll('button').find(b => b.text() === '发布定义')!.trigger('click')
    const textarea = wrapper.find('textarea')

    await textarea.setValue(JSON.stringify({ nodes: [{ id: 'n1' }] }))
    await wrapper.findAll('button').find(b => b.text() === '发布')!.trigger('click')
    expect(mockPublishDefinition).not.toHaveBeenCalled()

    await textarea.setValue(JSON.stringify({ name: 'x', nodes: [] }))
    await wrapper.findAll('button').find(b => b.text() === '发布')!.trigger('click')
    expect(mockPublishDefinition).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('发布成功：透传解析后的 body，成功提示并刷新定义列表', async () => {
    const wrapper = await mountView()
    await wrapper.findAll('button').find(b => b.text() === '发布定义')!.trigger('click')
    const body = { name: '新编排', description: '', nodes: [{ id: 'n1', prompt_template: 'p' }], edges: [] }
    await wrapper.find('textarea').setValue(JSON.stringify(body))
    await wrapper.findAll('button').find(b => b.text() === '发布')!.trigger('click')
    await nextTick()
    expect(mockPublishDefinition).toHaveBeenCalledTimes(1)
    expect(mockPublishDefinition).toHaveBeenCalledWith(body)
    expect(ElMessage.success).toHaveBeenCalled()
    expect(mockListDefinitions).toHaveBeenCalledTimes(2) // 挂载 1 次 + 发布后刷新 1 次
    wrapper.unmount()
  })

  it('运行展开行懒加载 steps：首次展开请求一次，重复展开不重复请求', async () => {
    const wrapper = await mountView()
    const btns = expandButtons(wrapper)
    expect(btns.length).toBe(2) // 定义表 + 运行表

    await btns[1].trigger('click')
    await nextTick()
    expect(mockGetRun).toHaveBeenCalledTimes(1)
    expect(mockGetRun).toHaveBeenCalledWith('run_1')

    // 再次展开同一行：steps 已缓存，不再请求
    await btns[1].trigger('click')
    await nextTick()
    expect(mockGetRun).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
