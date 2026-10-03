/**
 * OrchDagEditor 单测（P3-editor）：
 * - 初始化：definition → 保存 payload 结构对齐后端 validate_definition
 *   （nodes 全字段 + edges kind/condition 经 edge.data 往返保留）
 * - palette 添加节点 → 保存 payload 包含新节点
 * - node-click → 属性面板编辑 → 保存写回 name/label
 * - 预校验：空画布 / 环 → warning 拦截不 emit save
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
  // NodePropertiesPanel 内部 el-* 组件（编辑名称用）
  'el-form': { template: '<form><slot /></form>' },
  'el-form-item': { template: '<div><slot /></div>', props: ['label'] },
  'el-input': {
    props: ['modelValue', 'type', 'rows', 'placeholder'],
    emits: ['update:modelValue'],
    template: `<textarea v-if="type === 'textarea'" :value="modelValue"
        @input="$emit('update:modelValue', $event.target.value)" />
      <input v-else :value="modelValue"
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
    await w.find('input').setValue('抽取-改')
    await saveButton(w).trigger('click')
    const saved = (w.emitted('save')![0][0] as any).nodes.find((n: any) => n.id === 'a')
    expect(saved.name).toBe('抽取-改')
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
