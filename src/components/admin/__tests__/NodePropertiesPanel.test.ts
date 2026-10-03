/**
 * NodePropertiesPanel 单测（P3-editor）：
 * - agent 节点显示 prompt/model/超时字段；join 节点显示 join 策略
 * - 编辑字段 → emit update:node 携带单字段增量
 * - 未选中节点 → 空态提示
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
}

function mountPanel(node: Record<string, any> | null) {
  return mount(NodePropertiesPanel, { props: { node }, global: { stubs } })
}

describe('NodePropertiesPanel', () => {
  it('未选中节点 → 空态提示，无表单', () => {
    const w = mountPanel(null)
    expect(w.text()).toContain('点击节点查看属性')
    expect(w.find('form').exists()).toBe(false)
  })

  it('agent 节点：编辑名称 → emit update:node {name}', async () => {
    const w = mountPanel({ id: 'a', kind: 'agent', label: '步骤A', prompt_template: 'p' })
    await w.find('input').setValue('新步骤名')
    expect(w.emitted('update:node')).toEqual([[{ name: '新步骤名' }]])
  })

  it('agent 节点：编辑 Prompt 模板 → emit update:node {prompt_template}', async () => {
    const w = mountPanel({ id: 'a', kind: 'agent', label: 'A', prompt_template: '旧' })
    const promptArea = w.findAll('textarea')
      .find(t => t.element.value === '旧')!
    await promptArea.setValue('做一件事：{{input}}')
    expect(w.emitted('update:node')).toEqual([[{ prompt_template: '做一件事：{{input}}' }]])
  })

  it('agent 节点：编辑超时 → emit update:node {timeout_sec}', async () => {
    const w = mountPanel({ id: 'a', kind: 'agent', label: 'A', timeout_sec: 30 })
    const num = w.find('input[type="number"]')
    await num.setValue('120')
    expect(w.emitted('update:node')).toEqual([[{ timeout_sec: 120 }]])
  })

  it('join 节点：切换 Join 策略 → emit update:node {join_policy}', async () => {
    const w = mountPanel({ id: 'j', kind: 'join', label: '汇聚' })
    await w.find('select').setValue('any_success')
    expect(w.emitted('update:node')).toEqual([[{ join_policy: 'any_success' }]])
  })
})
