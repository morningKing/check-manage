import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import {
  buildToolSummary, genericResultSummary, resolveRenderer,
  stringifyRedacted, normalizeToolName,
} from '@/components/ai-chat/tool-renderers/registry'
import ToolCallBubble from '@/components/ai-chat/ToolCallBubble.vue'

describe('tool-renderers registry', () => {
  it('strips the check-manage_ prefix when matching', () => {
    expect(normalizeToolName('check-manage_query_collection')).toBe('query_collection')
    const { renderer } = resolveRenderer('check-manage_query_collection')
    expect(renderer?.displayName).toBe('查询数据页')
  })

  it('summarizes query_collection input and result', () => {
    const s = buildToolSummary({
      name: 'check-manage_query_collection', normalizedName: 'query_collection',
      input: { collection: '订单', keyword: '供应商', limit: 50 },
      result: { count: 24, rows: [] }, status: 'completed',
    })
    expect(s.actionText).toBe('查询数据页“订单”，关键词为“供应商”，最多 50 条')
    expect(s.resultText).toBe('找到 24 条记录')
    expect(s.statusText).toBe('已完成')
  })

  it('summarizes builtin file tools', () => {
    const read = buildToolSummary({ name: 'read', normalizedName: 'read', input: { filePath: 'src/app.ts' }, status: 'completed' })
    expect(read.actionText).toBe('读取文件“app.ts”')
    const bash = buildToolSummary({ name: 'bash', normalizedName: 'bash', input: { command: 'npm test' }, status: 'running' })
    expect(bash.actionText).toBe('执行命令“npm test”')
    expect(bash.statusText).toBe('正在执行')
  })

  it('degrades unknown tools to 调用工具“name” with optional target', () => {
    const s = buildToolSummary({ name: 'mystery_tool', normalizedName: 'mystery_tool', title: 'target-1', status: 'completed' })
    expect(s.actionText).toBe('调用工具“mystery_tool” · target-1')
    expect(s.resultText).toBe('执行完成，无返回结果')
  })

  it('maps every status to text, unknown included', () => {
    for (const [status, text] of [
      ['pending', '等待执行'], ['running', '正在执行'], ['completed', '已完成'],
      ['error', '执行失败'], ['cancelled', '已取消'], ['permission', '等待确认'],
      ['weird', '状态未知'],
    ] as const) {
      expect(buildToolSummary({ name: 'read', normalizedName: 'read', status }).statusText).toBe(text)
    }
  })

  it('surfaces error results with a readable reason', () => {
    const s = buildToolSummary({
      name: 'check-manage_query_collection', normalizedName: 'query_collection',
      input: { collection: '订单' }, result: { error: '数据页不存在' }, status: 'error',
    })
    expect(s.resultText).toContain('数据页不存在')
    expect(s.statusText).toBe('执行失败')
  })

  it('generic summary covers arrays / strings / empty', () => {
    expect(genericResultSummary([1, 2, 3])).toBe('找到 3 项')
    expect(genericResultSummary('first line\nsecond')).toBe('first line')
    expect(genericResultSummary(null)).toBe('执行完成，无返回结果')
    expect(genericResultSummary({ rows: [1, 2] })).toBe('找到 2 项')
  })

  it('masks sensitive fields in raw details', () => {
    const out = stringifyRedacted({ apiKey: 'sk-1234567890abcd', token: 'x', nested: { password: 'p@ss' } })
    expect(out).not.toContain('sk-1234567890abcd')
    expect(out).toContain('****')
  })

  it('long strings are truncated in summaries', () => {
    const s = buildToolSummary({
      name: 'read', normalizedName: 'read', input: { filePath: 'a.md' },
      result: 'x'.repeat(300), status: 'completed',
    })
    expect(s.resultText.length).toBeLessThanOrEqual(120)
  })
})

describe('ToolCallBubble.vue', () => {
  const stubs = { ElIcon: { template: '<i><slot/></i>' } }

  it('shows the summary by default, raw JSON only after expand', async () => {
    const w = mount(ToolCallBubble, {
      props: {
        name: 'check-manage_query_collection',
        input: { collection: '订单', keyword: '供应商' },
        result: { count: 24 }, status: 'completed', durationMs: 1200,
      },
      global: { stubs },
    })
    const head = w.find('.tool-call__head')
    expect(head.attributes('aria-expanded')).toBe('false')
    expect(head.text()).toContain('查询数据页“订单”')
    expect(head.text()).toContain('找到 24 条记录')
    expect(head.text()).toContain('已完成')
    // raw JSON body is hidden (v-show) until expanded; head shows summary only
    expect(w.find('.tool-call__body').attributes('style')).toContain('display: none')
    expect(head.text()).not.toContain('"collection"')
    await head.trigger('click')
    expect(w.find('.tool-call__head').attributes('aria-expanded')).toBe('true')
    expect(w.find('.tool-call__body').attributes('style')).not.toContain('display: none')
    expect(w.text()).toContain('"collection"')
  })

  it('status icon is never the only signal — text label always present', () => {
    const w = mount(ToolCallBubble, {
      props: { name: 'bash', status: 'running' },
      global: { stubs },
    })
    expect(w.find('.tool-call__status-text').text()).toBe('正在执行')
  })
})
