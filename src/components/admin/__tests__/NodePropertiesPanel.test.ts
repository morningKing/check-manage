/**
 * NodePropertiesPanel 单测（P3-editor）：
 * - node 模式：agent 节点显示 prompt/model/超时字段；join 节点显示 join 策略
 * - 编辑字段 → emit update:node 携带单字段增量
 * - edge 模式：编辑 condition field/op/value → emit update:edge(edgeId, 合并后完整条件)
 * - edge 模式：删除按钮 → emit remove-edge
 * - 未选中 → 空态提示
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import NodePropertiesPanel from '../NodePropertiesPanel.vue'

const stubs = {
  'el-form': { template: '<form><slot /></form>' },
  'el-form-item': { template: '<div><slot /></div>', props: ['label'] },
  // 文本输入：type=textarea 渲染 textarea，否则 input；值变更上抛 update:modelValue
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
  'el-button': {
    props: ['type', 'size'],
    emits: ['click'],
    template: '<button @click="$emit(\'click\')"><slot /></button>',
  },
}

function mountPanel(selected: any) {
  return mount(NodePropertiesPanel, { props: { selected }, global: { stubs } })
}

function nodeSelection(data: Record<string, any>) {
  return { type: 'node' as const, data }
}

describe('NodePropertiesPanel', () => {
  it('未选中 → 空态提示，无表单', () => {
    const w = mountPanel(null)
    expect(w.text()).toContain('点击节点或边查看属性')
    expect(w.find('form').exists()).toBe(false)
  })

  it('node 模式（agent）：编辑名称 → emit update:node {name}', async () => {
    const w = mountPanel(nodeSelection({ id: 'a', kind: 'agent', label: '步骤A', prompt_template: 'p' }))
    await w.find('input').setValue('新步骤名')
    expect(w.emitted('update:node')).toEqual([[{ name: '新步骤名' }]])
  })

  it('node 模式（agent）：编辑 Prompt 模板 → emit update:node {prompt_template}', async () => {
    const w = mountPanel(nodeSelection({ id: 'a', kind: 'agent', label: 'A', prompt_template: '旧' }))
    const promptArea = w.findAll('textarea')
      .find(t => t.element.value === '旧')!
    await promptArea.setValue('做一件事：{{input}}')
    expect(w.emitted('update:node')).toEqual([[{ prompt_template: '做一件事：{{input}}' }]])
  })

  it('node 模式（agent）：编辑超时 → emit update:node {timeout_sec}', async () => {
    const w = mountPanel(nodeSelection({ id: 'a', kind: 'agent', label: 'A', timeout_sec: 30 }))
    const num = w.find('input[type="number"]')
    await num.setValue('120')
    expect(w.emitted('update:node')).toEqual([[{ timeout_sec: 120 }]])
  })

  it('node 模式（join）：切换 Join 策略 → emit update:node {join_policy}', async () => {
    const w = mountPanel(nodeSelection({ id: 'j', kind: 'join', label: '汇聚' }))
    await w.find('select').setValue('any_success')
    expect(w.emitted('update:node')).toEqual([[{ join_policy: 'any_success' }]])
  })

  // ── edge 模式（遗留 2）────────────────────────────────────────────
  const edgeSelection = {
    type: 'edge' as const,
    data: {
      id: 'e1-a-b', source: 'a', target: 'b',
      condition: { field: 'text', op: 'contains', value: 'ok' },
    },
  }

  it('edge 模式：显示条件边标题与 field/op/value 三字段', () => {
    const w = mountPanel(edgeSelection)
    expect(w.text()).toContain('条件边 — a → b')
    // 两个文本输入（field/value）+ 一个下拉（op）
    expect(w.findAll('input')).toHaveLength(2)
    expect(w.find('select').element.value).toBe('contains')
  })

  it('edge 模式：编辑 field → emit update:edge(edgeId, 合并完整条件)', async () => {
    const w = mountPanel(edgeSelection)
    await w.findAll('input')[0].setValue('status')
    expect(w.emitted('update:edge')).toEqual([
      ['e1-a-b', { field: 'status', op: 'contains', value: 'ok' }],
    ])
  })

  it('edge 模式：切换 op → emit update:edge 保留其余字段', async () => {
    const w = mountPanel(edgeSelection)
    await w.find('select').setValue('==')
    expect(w.emitted('update:edge')).toEqual([
      ['e1-a-b', { field: 'text', op: '==', value: 'ok' }],
    ])
  })

  it('edge 模式：编辑 value → emit update:edge 保留其余字段', async () => {
    const w = mountPanel(edgeSelection)
    await w.findAll('input')[1].setValue('done')
    expect(w.emitted('update:edge')).toEqual([
      ['e1-a-b', { field: 'text', op: 'contains', value: 'done' }],
    ])
  })

  it('edge 模式：点击删除 → emit remove-edge(edgeId)', async () => {
    const w = mountPanel(edgeSelection)
    await w.find('button').trigger('click')
    expect(w.emitted('remove-edge')).toEqual([['e1-a-b']])
  })

  it('edge 模式：无边 condition 时不报错，编辑补默认 op=contains', async () => {
    const w = mountPanel({ type: 'edge' as const, data: { id: 'e0-a-b', source: 'a', target: 'b' } })
    await w.findAll('input')[0].setValue('text')
    expect(w.emitted('update:edge')).toEqual([
      ['e0-a-b', { field: 'text', op: 'contains', value: '' }],
    ])
  })
})
