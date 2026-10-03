/**
 * AiOrchestrationManager 组件单测（P3-A7 + P3-editor）
 *
 * 覆盖：
 * - 挂载时并行拉取定义与运行列表（definitions / runs 集合键）
 * - 发布对话框：JSON 解析失败 / 缺 name / nodes 为空 → 拦截且不发请求
 * - 发布成功 → publishDefinition 收到解析后的 body，成功提示 + 刷新定义列表
 * - 运行展开行懒加载 steps：首次展开调 getRun，重复展开不重复请求
 * - DAG 编辑器（P3-editor）：编辑按钮 → getDefinition 加载详情 →
 *   编辑器 save → publishDefinition 发布新版本并刷新；详情加载失败关编辑器
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
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
const mockGetDefinition = vi.fn()
const mockPublishDefinition = vi.fn()
const mockListRuns = vi.fn()
const mockGetRun = vi.fn()

vi.mock('@/api/orchestration', () => ({
  listDefinitions: (...args: any[]) => mockListDefinitions(...args),
  getDefinition: (...args: any[]) => mockGetDefinition(...args),
  publishDefinition: (...args: any[]) => mockPublishDefinition(...args),
  listRuns: (...args: any[]) => mockListRuns(...args),
  getRun: (...args: any[]) => mockGetRun(...args),
}))

import AiOrchestrationManager from '../AiOrchestrationManager.vue'
import { ElMessage } from 'element-plus'

// el-table stub：给运行表一个触发 expand-change 的按钮（定义表不受影响）；
// 渲染列 slot（P3-editor：定义表的操作列里有「编辑」按钮）
const ElTableStub = {
  template: `<div>
    <button class="stub-trigger-expand" @click="$emit('expand-change', data[0], [data[0]])" />
    <slot />
  </div>`,
  props: ['data', 'rowKey'],
  emits: ['expand-change'],
}

// 列 stub：沿 $parent 链找表 data（test-utils stub 下 provide/inject 不贯通），
// 逐行渲染列的 scoped slot
const ElTableColumnStub = {
  template: `<div class="stub-column">
    <template v-for="(row, i) in rows" :key="i"><slot :row="row" /></template>
  </div>`,
  computed: {
    rows(this: any): any[] {
      let p: any = this.$parent
      while (p) {
        if (Array.isArray(p.data)) return p.data
        p = p.$parent
      }
      return []
    },
  },
}

// DAG 编辑器 stub：点击按钮即 emit save（画布交互已在 OrchDagEditor.test.ts 覆盖）
const OrchDagEditorStub = {
  template: `<div class="stub-dag-editor">
    <button class="stub-editor-save" @click="$emit('save', savePayload)" />
  </div>`,
  props: ['definition'],
  emits: ['save', 'cancel'],
  data() {
    return {
      savePayload: {
        id: 'orch_a', name: '编排A',
        nodes: [{ id: 'step-1', kind: 'agent', name: '第一步', prompt_template: 'p' }],
        edges: [],
      },
    }
  },
}

const stubs = {
  OrchDagEditor: OrchDagEditorStub,
  'el-tabs': { template: '<div><slot /></div>', props: ['modelValue'] },
  'el-tab-pane': { template: '<div><slot /></div>', props: ['label', 'name'] },
  // 列 slot 逐行渲染（P3-editor：定义表操作列的「编辑」按钮可点）
  'el-table': ElTableStub,
  'el-table-column': ElTableColumnStub,
  'el-button': {
    template: '<button @click="$emit(\'click\')"><slot /></button>',
    props: ['type', 'plain', 'loading', 'size'],
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
    mockGetDefinition.mockResolvedValue({
      ...sampleDefinitions[0],
      nodes: [{ id: 'step-1', kind: 'agent', name: '第一步', prompt_template: 'p' }],
      edges: [{ source: 'step-1', target: 'step-2', kind: 'advance' }],
    })
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

  it('DAG 编辑器：编辑按钮 → 加载定义详情 → 编辑器携带 definition', async () => {
    const wrapper = await mountView()
    await wrapper.findAll('button').find(b => b.text() === '编辑')!.trigger('click')
    await flushPromises()
    expect(mockGetDefinition).toHaveBeenCalledTimes(1)
    expect(mockGetDefinition).toHaveBeenCalledWith('orch_a')
    const editor = wrapper.findComponent(OrchDagEditorStub)
    expect(editor.exists()).toBe(true)
    const definition = (editor.vm as any).definition
    expect(definition.id).toBe('orch_a')
    expect(definition.nodes).toHaveLength(1)
    wrapper.unmount()
  })

  it('DAG 编辑器：save → publishDefinition 发布新版本并刷新定义列表', async () => {
    const wrapper = await mountView()
    await wrapper.findAll('button').find(b => b.text() === '编辑')!.trigger('click')
    await flushPromises()
    await wrapper.find('.stub-editor-save').trigger('click')
    await flushPromises()
    expect(mockPublishDefinition).toHaveBeenCalledTimes(1)
    const body = mockPublishDefinition.mock.calls[0][0]
    expect(body.id).toBe('orch_a')
    expect(body.name).toBe('编排A')
    expect(body.nodes).toEqual([
      { id: 'step-1', kind: 'agent', name: '第一步', prompt_template: 'p' },
    ])
    expect(ElMessage.success).toHaveBeenCalled()
    expect(mockListDefinitions).toHaveBeenCalledTimes(2) // 挂载 + 保存后刷新
    wrapper.unmount()
  })

  it('DAG 编辑器：详情加载失败 → 编辑器不渲染', async () => {
    const wrapper = await mountView()
    mockGetDefinition.mockRejectedValue(new Error('not found'))
    await wrapper.findAll('button').find(b => b.text() === '编辑')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.stub-dag-editor').exists()).toBe(false)
    wrapper.unmount()
  })
})
