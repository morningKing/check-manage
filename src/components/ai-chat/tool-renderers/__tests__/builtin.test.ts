import { describe, it, expect } from 'vitest'
import { builtinRenderers } from '../builtin'

function find(name: string) {
  const r = builtinRenderers.find(x => x.matches.includes(name))
  expect(r, `renderer for ${name}`).toBeTruthy()
  return r!
}

describe('builtin tool renderers (P2 §5.4 自然语言摘要)', () => {
  it('bash: summarizes command, truncated to 80 chars', () => {
    const r = find('bash')
    expect(r.inputSummary!({ command: 'ls -la' })).toBe('执行命令“ls -la”')
    expect(r.inputSummary!({ cmd: 'x'.repeat(120) })).toContain('…')
    expect(r.inputSummary!({})).toBe('执行命令')
  })

  it('bash: failure result carries 命令失败 prefix', () => {
    const r = find('bash')
    expect(r.resultSummary!({ error: 'exit 1' })).toBe('命令失败：exit 1')
    expect(r.resultSummary!('line1\nline2')).toBe('line1')
    expect(r.resultSummary!('')).toBeUndefined()
  })

  it('read/write/edit: file name basename (path not leaked)', () => {
    for (const tool of ['read', 'write', 'edit'] as const) {
      const r = find(tool)
      expect(r.inputSummary!({ filePath: '/a/b/c.md' })).toContain('c.md')
      expect(r.inputSummary!({ filePath: '/a/b/c.md' })).not.toContain('/a/b')
      expect(r.inputSummary!({})).toBe(r.displayName)
    }
  })

  it('glob/grep: array results report counts', () => {
    expect(find('glob').resultSummary!(['a', 'b'])).toBe('匹配到 2 个文件')
    expect(find('grep').resultSummary!(['x'])).toBe('命中 1 处')
    expect(find('glob').resultSummary!('not-array')).toBeUndefined()
  })

  it('task: 委托子代理 with agent name and description', () => {
    const r = find('task')
    expect(r.inputSummary!({ description: '扫描日志', subagent_type: 'reviewer' }))
      .toBe('委托子代理处理任务（reviewer）：扫描日志')
    expect(r.inputSummary!({ agent: 'a1' })).toBe('委托子代理处理任务（a1）')
    expect(r.inputSummary!({})).toBe('委托子代理处理任务')
  })

  it('todo tools hide raw input (showRawInput=false)', () => {
    for (const t of ['todowrite', 'todoread']) {
      expect(find(t).showRawInput).toBe(false)
    }
  })

  it('webfetch: summarizes url', () => {
    expect(find('webfetch').inputSummary!({ url: 'https://example.com' }))
      .toBe('抓取网页 https://example.com')
  })
})
