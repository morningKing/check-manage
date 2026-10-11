import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import GateCheckRows, {
  emptyEditRow, prefillEditRows, serializeChecks,
} from '../GateCheckRows.vue'
import type { GateCheckEditRow } from '../GateCheckRows.vue'

const stubs = {
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
  'el-option': { template: '<option />' },
  'el-radio-group': { template: '<div><slot /></div>', props: ['modelValue'] },
  'el-radio-button': { template: '<label><slot /></label>', props: ['value'] },
  'el-checkbox': {
    template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
    props: ['modelValue'],
    emits: ['update:modelValue'],
  },
  'el-button': { template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>', props: ['disabled'], emits: ['click'] },
  'el-input-number': true,
}

describe('serializeChecks 提交归一（spec §8.4）', () => {
  it('post/tool 行 = 白名单重建,行内部字段不落 payload,不完整行过滤', () => {
    const rows = [
      { ...emptyEditRow(), name: '克隆目标仓库', args_pattern: 'git clone' },
      { ...emptyEditRow() },  // 空 name/args_pattern → 过滤
    ] as GateCheckEditRow[]
    expect(serializeChecks(rows)).toEqual([
      { name: '克隆目标仓库', check_type: 'tool', tool: 'bash',
        args_pattern: 'git clone', min_count: 1 },
    ])
  })

  it('verifier 行 = {name, check_type, rubric[, subagents]},不带 tool/args_pattern', () => {
    const rows: GateCheckEditRow[] = [{
      ...emptyEditRow(), name: '结论含标记', check_type: 'verifier',
      tool: '', args_pattern: '', rubric: '回复包含 MARK', subagents: 'general',
    }]
    expect(serializeChecks(rows)).toEqual([
      { name: '结论含标记', check_type: 'verifier', rubric: '回复包含 MARK',
        subagents: ['general'] },
    ])
  })

  it('checkpoint 行 mode 键齐 + assert_spec;pre 行 round-trip:raw 未建模键透传', () => {
    const checkpoint: GateCheckEditRow = {
      ...emptyEditRow(), name: '写前读知识库', args_pattern: 'outputs/report',
      mode: 'checkpoint', timing: 'after', prompt: '请先读 docs/kb/', max_checks: 2,
      hasAssert: true, assert_tool: 'read', assert_pattern: 'docs/kb/',
      assert_min_count: 1,
    }
    expect(serializeChecks([checkpoint])).toEqual([{
      name: '写前读知识库', mode: 'checkpoint', check_type: 'tool', tool: 'bash',
      args_pattern: 'outputs/report', timing: 'after', prompt: '请先读 docs/kb/',
      max_checks: 2,
      assert_spec: { tool: 'read', args_pattern: 'docs/kb/', min_count: 1 },
    }])

    const pre: GateCheckEditRow = {
      ...emptyEditRow(), name: '禁删产物 v2', args_pattern: 'rm\\s+-rf',
      mode: 'pre', min_count: 3,
      raw: { name: '禁删产物', mode: 'pre', check_type: 'tool', tool: 'bash',
             args_pattern: 'rm\\s+-rf', min_count: 99,
             apply_to: { input_file_glob: 'docs/**' } },
    }
    expect(serializeChecks([pre])).toEqual([
      { name: '禁删产物 v2', mode: 'pre', check_type: 'tool', tool: 'bash',
        args_pattern: 'rm\\s+-rf', min_count: 3,
        apply_to: { input_file_glob: 'docs/**' } },
    ])
  })
})

describe('prefillEditRows 回填 round-trip', () => {
  it('checkpoint/pre/verifier 行不降级;raw 只挂 pre 行', () => {
    const saved = [
      // 后端 validate_checks 归一后的形状
      { name: '写前读知识库', mode: 'checkpoint', check_type: 'tool', tool: 'write',
        args_pattern: 'outputs/report', timing: 'before', prompt: '先读知识库',
        max_checks: 2, assert_spec: { tool: 'read', args_pattern: 'docs/kb/', min_count: 1 },
        subagents: null },
      { name: '禁删产物', mode: 'pre', check_type: 'tool', tool: 'bash',
        args_pattern: 'rm\\s+-rf', min_count: 1, subagents: ['general'],
        apply_to: { input_file_glob: 'docs/**' } },
      { name: '结论含标记', check_type: 'verifier', tool: 'verifier',
        args_pattern: '', min_count: 1, effect_spec: { rubric: '回复包含 MARK' },
        subagents: null },
      { name: '克隆目标仓库', check_type: 'tool', tool: 'bash',
        args_pattern: 'git clone', min_count: 1, subagents: null },
    ]
    const rows = prefillEditRows(saved)
    expect(rows).toHaveLength(4)
    // checkpoint:timing/prompt/max_checks/assert_* 回填成行内部编辑字段
    expect(rows[0]).toMatchObject({
      mode: 'checkpoint', timing: 'before', prompt: '先读知识库', max_checks: 2,
      hasAssert: true, assert_tool: 'read', assert_pattern: 'docs/kb/',
    })
    expect(rows[0].raw).toBeUndefined()
    // pre:mode 保留 + raw 留存原始行(apply_to 不丢)
    expect(rows[1]).toMatchObject({ mode: 'pre', subagents: 'general' })
    expect(rows[1].raw).toEqual(saved[1])
    // verifier:rubric 从 effect_spec 取出
    expect(rows[2]).toMatchObject({ check_type: 'verifier', rubric: '回复包含 MARK' })
    // post 行为缺省模式
    expect(rows[3]).toMatchObject({ mode: 'post', args_pattern: 'git clone' })
    // 回填后再提交:pre 行 deny 语义与 apply_to 不丢
    expect(serializeChecks(rows)[1]).toEqual({
      name: '禁删产物', mode: 'pre', check_type: 'tool', tool: 'bash',
      args_pattern: 'rm\\s+-rf', min_count: 1, subagents: ['general'],
      apply_to: { input_file_glob: 'docs/**' },
    })
  })

  it('空/null 输入返回空数组', () => {
    expect(prefillEditRows(null)).toEqual([])
    expect(prefillEditRows([])).toEqual([])
  })
})

describe('GateCheckRows 组件', () => {
  it('渲染行卡片;加一条/删除走 update:modelValue', async () => {
    const w = mount(GateCheckRows, {
      props: { modelValue: [emptyEditRow()] },
      global: { stubs },
    })
    expect(w.findAll('.gate-card')).toHaveLength(1)
    // 单行时删除禁用
    const del = w.findAll('button').find(b => b.text() === '删除')!
    expect((del.element as HTMLButtonElement).disabled).toBe(true)
    await w.find('button[data-test="gate-add"]').trigger('click')
    expect(w.emitted('update:modelValue')![0][0]).toHaveLength(2)
    await w.setProps({ modelValue: w.emitted('update:modelValue')![0][0] as GateCheckEditRow[] })
    const del2 = w.findAll('button').find(b => b.text() === '删除')!
    expect((del2.element as HTMLButtonElement).disabled).toBe(false)
    await del2.trigger('click')
    const emittedRows = w.emitted('update:modelValue')!
    expect(emittedRows[emittedRows.length - 1][0] as GateCheckEditRow[]).toHaveLength(1)
  })
})
