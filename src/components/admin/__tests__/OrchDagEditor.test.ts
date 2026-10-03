/**
 * OrchDagEditor 单测（P3-editor）：
 * - 初始化：definition → 保存 payload 结构对齐后端 validate_definition
 *   （nodes 全字段 + edges kind/condition 经 edge.data 往返保留；
 *   name/description 取自工具栏输入）
 * - palette 添加节点 → 保存 payload 包含新节点
 * - node-click → 属性面板编辑 → 保存写回 name/label
 * - edge-click → 面板切 edge 模式 → 编辑条件/删除边 → 保存写回（遗留 2）
 * - 工具栏改 name → save 携带（遗留 3）
 * - pane-click 清空选择；预校验：空画布 / 环 → warning 拦截不 emit save
 * - 取消 → emit cancel
 *
 * Vue Flow 以最小 mock 顶替（jsdom 不渲染 canvas）：props 同步进 hoisted
 * store，onConnect 捕获回调供测试直接触发连线。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

// 与 @vue-flow/core mock 工厂共享的画布状态（vi.mock 工厂被提升，须经 hoisted）
const h = vi.hoisted(() => ({
  nodes: [] as any[],
  edges: [] as any[],
  connectHandler: null as null | ((params: any) => void),
}))

vi.mock('element-plus', () => ({
  ElMessage: { warning: vi.fn(), success: vi.fn(), error: vi.fn() },
}))

vi.mock('@vue-flow/core', () => ({
  VueFlow: {
    name: 'VueFlow',
    props: ['nodes', 'edges', 'nodeTypes', 'fitViewOnInit'],
    created(this: any) {
      // 模拟 Vue Flow 把 :nodes/:edges 种进内部 store
      h.nodes = [...(this.nodes ?? [])]
      h.edges = [...(this.edges ?? [])]
    },
    template: '<div class="mock-vue-flow" />',
  },
  useVueFlow: () => ({
    addNodes: (ns: any[]) => { h.nodes.push(...ns) },
    addEdges: (es: any[]) => { h.edges.push(...es) },
    findNode: (id: string) => h.nodes.find(n => n.id === id),
    findEdge: (id: string) => h.edges.find(e => e.id === id),
    removeEdges: (items: any[]) => {
      const ids = new Set(items.map(x => (typeof x === 'string' ? x : x.id)))
      h.edges = h.edges.filter(e => !ids.has(e.id))
    },
    getNodes: { get value() { return h.nodes } },
    getEdges: { get value() { return h.edges } },
    onConnect: (fn: (params: any) => void) => { h.connectHandler = fn },
  }),
  Handle: { template: '<div />' },
  Position: { Top: 'top', Bottom: 'bottom' },
}))
vi.mock('@vue-flow/background', () => ({ Background: { template: '<div />' } }))
vi.mock('@vue-flow/controls', () => ({ Controls: { template: '<div />' } }))

import OrchDagEditor from '../OrchDagEditor.vue'
import { ElMessage } from 'element-plus'

const stubs = {
  'el-button': {
    template: '<button @click="$emit(\'click\')"><slot /></button>',
    props: ['type', 'size'],
    emits: ['click'],
  },
  // NodePropertiesPanel 内部 el-* 组件（编辑名称/条件用）
  'el-form': { template: '<form><slot /></form>' },
  'el-form-item': { template: '<div><slot /></div>', props: ['label'] },
  'el-input': {
    props: ['modelValue', 'type', 'rows', 'placeholder'],
    emits: ['update:modelValue'],
    template: `<textarea v-if="type === 'textarea'" :value="modelValue" :placeholder="placeholder"
        @input="$emit('update:modelValue', $event.target.value)" />
      <input v-else :value="modelValue" :placeholder="placeholder"
        @input="$emit('update:modelValue', $event.target.value)" />`,
  },
  'el-input-number': {
    props: ['modelValue', 'min'],
    emits: ['update:modelValue'],
    template: `<input type="number" :value="modelValue"
      @input="$emit('update:modelValue', Number($event.target.value))" />`,
  },
  'el-select': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template: `<select :value="modelValue"
      @change="$emit('update:modelValue', $event.target.value)"><slot /></select>`,
  },
  'el-option': { template: '<option :value="value">{{ label }}</option>', props: ['value', 'label'] },
}

const def = {
  id: 'orch_a',
  name: '编排A',
  nodes: [
    { id: 'a', kind: 'agent', name: '抽取', prompt_template: '做抽取', model: 'gpt', priority: 2, timeout_sec: 60 },
    { id: 'b', kind: 'join', name: '汇总', join_policy: 'any_success' },
  ],
  edges: [
    { source: 'a', target: 'b', kind: 'advance' },
    { source: 'a', target: 'b', kind: 'compensation', condition: { field: 'text', op: 'contains', value: 'ok' } },
  ],
}

function mountEditor(definition: any) {
  return mount(OrchDagEditor, { props: { definition }, global: { stubs } })
}

function saveButton(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll('button').find(b => b.text() === '保存')!
}

describe('OrchDagEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    h.nodes = []
    h.edges = []
    h.connectHandler = null
  })

  it('初始化：保存 payload 与 definition 结构对齐（edges kind/condition 保留）', async () => {
    const w = mountEditor(def)
    await saveButton(w).trigger('click')
    expect(w.emitted('save')).toHaveLength(1)
    expect(w.emitted('save')![0][0]).toEqual({
      id: 'orch_a',
      name: '编排A',
      description: '',
      nodes: [
        { id: 'a', kind: 'agent', name: '抽取', prompt_template: '做抽取', model: 'gpt',
          skills: [], budget: {}, timeout_sec: 60, join_policy: undefined, priority: 2 },
        { id: 'b', kind: 'join', name: '汇总', prompt_template: '', model: '',
          skills: [], budget: {}, timeout_sec: undefined, join_policy: 'any_success', priority: 0 },
      ],
      edges: [
        { source: 'a', target: 'b', kind: 'advance' },
        { source: 'a', target: 'b', kind: 'compensation', condition: { field: 'text', op: 'contains', value: 'ok' } },
      ],
    })
    w.unmount()
  })

  it('palette 添加节点 → 保存 payload 包含新节点（默认字段齐全）', async () => {
    const w = mountEditor(def)
    await w.find('.node-palette__item[data-kind="approval"]').trigger('click')
    await saveButton(w).trigger('click')
    const nodes = (w.emitted('save')![0][0] as any).nodes
    expect(nodes).toHaveLength(3)
    const added = nodes.find((n: any) => n.kind === 'approval')
    expect(added.id).toMatch(/^approval-/)
    expect(added.prompt_template).toBe('')
    expect(added.budget).toEqual({})
    w.unmount()
  })

  it('node-click 选中节点 → 属性面板改名 → 保存写回 name/label', async () => {
    const w = mountEditor(def)
    const flow = w.findComponent({ name: 'VueFlow' })
    flow.vm.$emit('node-click', {
      node: { id: 'a', data: { nodeId: 'a', label: '抽取', kind: 'agent', prompt_template: '做抽取' } },
    })
    await nextTick()
    expect(w.text()).toContain('属性 — agent')
    // 注意：工具栏有 name/description 两个输入，须限定属性面板内的输入框
    await w.find('.node-props input').setValue('抽取-改')
    await saveButton(w).trigger('click')
    const saved = (w.emitted('save')![0][0] as any).nodes.find((n: any) => n.id === 'a')
    expect(saved.name).toBe('抽取-改')
    w.unmount()
  })

  // ── 条件边属性编辑（遗留 2）──────────────────────────────────────
  const compensationEdge = {
    id: 'e1-a-b', source: 'a', target: 'b',
    data: { kind: 'compensation', condition: { field: 'text', op: 'contains', value: 'ok' } },
  }

  it('edge-click → 面板切 edge 模式；编辑条件 → 保存写回 condition', async () => {
    const w = mountEditor(def)
    const flow = w.findComponent({ name: 'VueFlow' })
    flow.vm.$emit('edge-click', { edge: compensationEdge })
    await nextTick()
    expect(w.text()).toContain('条件边 — a → b')
    // 面板第一个输入 = 条件 field
    await w.find('.node-props input').setValue('status')
    await saveButton(w).trigger('click')
    const edges = (w.emitted('save')![0][0] as any).edges
    expect(edges[1].condition).toEqual({ field: 'status', op: 'contains', value: 'ok' })
    w.unmount()
  })

  it('edge 面板切换 op → 保存写回新 op', async () => {
    const w = mountEditor(def)
    const flow = w.findComponent({ name: 'VueFlow' })
    flow.vm.$emit('edge-click', { edge: compensationEdge })
    await nextTick()
    await w.find('.node-props select').setValue('not_contains')
    await saveButton(w).trigger('click')
    const edges = (w.emitted('save')![0][0] as any).edges
    expect(edges[1].condition).toEqual({ field: 'text', op: 'not_contains', value: 'ok' })
    w.unmount()
  })

  it('edge 面板删除按钮 → 边被移除，保存不含该边且清空面板', async () => {
    const w = mountEditor(def)
    const flow = w.findComponent({ name: 'VueFlow' })
    flow.vm.$emit('edge-click', { edge: compensationEdge })
    await nextTick()
    await w.find('.node-props button').trigger('click')
    await saveButton(w).trigger('click')
    const edges = (w.emitted('save')![0][0] as any).edges
    expect(edges).toEqual([{ source: 'a', target: 'b', kind: 'advance' }])
    expect(w.text()).toContain('点击节点或边查看属性')
    w.unmount()
  })

  it('pane-click → 清空节点/边选择，面板回空态', async () => {
    const w = mountEditor(def)
    const flow = w.findComponent({ name: 'VueFlow' })
    flow.vm.$emit('node-click', { node: { id: 'a', data: { nodeId: 'a', label: '抽取', kind: 'agent' } } })
    await nextTick()
    expect(w.text()).toContain('属性 — agent')
    flow.vm.$emit('pane-click', {})
    await nextTick()
    expect(w.text()).toContain('点击节点或边查看属性')
    w.unmount()
  })

  // ── 定义名称/描述编辑（遗留 3）──────────────────────────────────
  it('工具栏编辑 name → save 携带编辑后的 name', async () => {
    const w = mountEditor(def)
    await w.find('input[placeholder="定义名称"]').setValue('编排A-改名')
    await saveButton(w).trigger('click')
    expect((w.emitted('save')![0][0] as any).name).toBe('编排A-改名')
    w.unmount()
  })

  it('工具栏编辑 description → save 携带 description', async () => {
    const w = mountEditor(def)
    await w.find('input[placeholder="描述（可选）"]').setValue('新描述')
    await saveButton(w).trigger('click')
    expect((w.emitted('save')![0][0] as any).description).toBe('新描述')
    w.unmount()
  })

  it('连线（onConnect）→ 新边默认 advance', async () => {
    const w = mountEditor({ ...def, edges: [] })
    h.connectHandler?.({ source: 'a', target: 'b' })
    await saveButton(w).trigger('click')
    expect(w.emitted('save')).toHaveLength(1)
    expect((w.emitted('save')![0][0] as any).edges)
      .toEqual([{ source: 'a', target: 'b', kind: 'advance' }])
    w.unmount()
  })

  it('预校验：连线成环 → warning 且不 emit save', async () => {
    const w = mountEditor(def)
    h.connectHandler?.({ source: 'b', target: 'a' }) // 与 a→b 成环
    await saveButton(w).trigger('click')
    expect(ElMessage.warning).toHaveBeenCalled()
    expect(w.emitted('save')).toBeUndefined()
    w.unmount()
  })

  it('预校验：空画布 → warning 且不 emit save', async () => {
    const w = mountEditor(null)
    await saveButton(w).trigger('click')
    expect(ElMessage.warning).toHaveBeenCalled()
    expect(w.emitted('save')).toBeUndefined()
    w.unmount()
  })

  it('取消 → emit cancel', async () => {
    const w = mountEditor(def)
    await w.findAll('button').find(b => b.text() === '取消')!.trigger('click')
    expect(w.emitted('cancel')).toHaveLength(1)
    w.unmount()
  })
})
