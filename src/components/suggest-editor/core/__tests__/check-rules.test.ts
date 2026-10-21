import { describe, it, expect } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import { compileRules, runCheckRules, type LocalCheckRuleInput } from '../check-rules'

function docOf(html: string) {
  const editor = new Editor({ extensions: [StarterKit], content: html })
  const doc = editor.state.doc
  editor.destroy()
  return doc
}
const R = (r: Partial<LocalCheckRuleInput>): LocalCheckRuleInput =>
  ({ id: 'r', name: '规则', enabled: true, severity: 'warn', ...r } as LocalCheckRuleInput)

describe('maps', () => {
  it('命中错误写法，replacement=正确写法，可接受（notice=false）', () => {
    const m = runCheckRules(docOf('<p>设备 S-5700 上线</p>'),
      compileRules([R({ maps: [{ wrong: 'S-5700', right: 'S5700' }] })]))
    expect(m).toHaveLength(1)
    expect(m[0]).toMatchObject({ ruleId: 'r', replacement: 'S5700', notice: false, text: 'S-5700' })
    expect(m[0].reason).toContain('[规则]')
  })
  it('大小写敏感开关', () => {
    const rule = R({ id: 'c', maps: [{ wrong: 's-5700', right: 'S5700', caseSensitive: true }] })
    expect(runCheckRules(docOf('<p>S-5700</p>'), compileRules([rule]))).toHaveLength(0)
  })
})

describe('patterns', () => {
  it('提取候选但不合规 → notice', () => {
    const rule = R({ id: 'p', patterns: [{ extract: '[A-Z]{2,4}-?\\d{3,4}', valid: '^S\\d{4}$', message: '型号格式' }] })
    const m = runCheckRules(docOf('<p>单板 AB-123 与 S6700</p>'), compileRules([rule]))
    expect(m).toHaveLength(1)
    expect(m[0].text).toBe('AB-123')
    expect(m[0].notice).toBe(true)
    expect(m[0].replacement).toBeNull()
  })
  it('flags 含 g 时自动去重，仍能大小写不敏感匹配', () => {
    const rule = R({ id: 'pf', patterns: [{ extract: 'ab-\\d{3}', valid: '^AB-\\d{3}$', message: '格式', flags: 'gi' }] })
    const m = runCheckRules(docOf('<p>ab-123</p>'), compileRules([rule]))
    expect(m).toHaveLength(1)
    expect(m[0]).toMatchObject({ text: 'ab-123', notice: true })
  })
})

describe('whitelists', () => {
  it('锚点后的值不在白名单 → notice；在白名单 → 无', () => {
    const rule = R({ id: 'w', whitelists: [{ anchors: ['设备类型：', '设备类型:'],
      values: ['S5700', 'S6700'], message: '不在清单' }] })
    const compiled = compileRules([rule])
    expect(runCheckRules(docOf('<p>设备类型：S9999</p>'), compiled)).toHaveLength(1)
    expect(runCheckRules(docOf('<p>设备类型：S5700</p>'), compiled)).toHaveLength(0)
  })
  it('锚点与值之间允许空格', () => {
    const rule = R({ id: 'w2', whitelists: [{ anchors: ['型号：'], values: ['A1'], message: 'm' }] })
    expect(runCheckRules(docOf('<p>型号：  B2</p>'), compileRules([rule]))).toHaveLength(1)
  })
  it('锚点后多个值（；分隔）：逐个校验，非法值命中', () => {
    const rule = R({ id: 'wm', whitelists: [{ anchors: ['设备类型：', '设备类型:'],
      values: ['9800M12', '9600M12'], message: '设备类型只能是 9800M12 或 9600M12' }] })
    const m = runCheckRules(docOf('<p>设备类型：9800M12；5000M12</p>'), compileRules([rule]))
    expect(m).toHaveLength(1)
    expect(m[0]).toMatchObject({ text: '5000M12', notice: true })
    expect(m[0].reason).toContain('设备类型只能是')
  })
  it('多个值全部合法（中英文分隔符/连接词）→ 无', () => {
    const rule = R({ id: 'wok', whitelists: [{ anchors: ['设备类型：'],
      values: ['9800M12', '9600M12'], message: 'm' }] })
    const compiled = compileRules([rule])
    expect(runCheckRules(docOf('<p>设备类型：9800M12、9600M12</p>'), compiled)).toHaveLength(0)
    expect(runCheckRules(docOf('<p>设备类型：9800M12 或 9600M12</p>'), compiled)).toHaveLength(0)
    expect(runCheckRules(docOf('<p>设备类型：9800M12和9600M12</p>'), compiled)).toHaveLength(0)
  })
  it('值列表遇到非分隔符即停止，不把后文误当值', () => {
    const rule = R({ id: 'wstop', whitelists: [{ anchors: ['设备类型：'],
      values: ['9800M12'], message: 'm' }] })
    const compiled = compileRules([rule])
    expect(runCheckRules(docOf('<p>设备类型：9800M12（不涉及拖带）5000M12</p>'), compiled)).toHaveLength(0)
    expect(runCheckRules(docOf('<p>设备类型：9800M12 台数 3</p>'), compiled)).toHaveLength(0)
  })
})

describe('presence', () => {
  const rule = R({ id: 'pc', presence: [{ triggers: ['9800M12'],
    requiresAny: ['9800UPS', '不涉及拖带'], message: '缺拖带说明' }] })
  it('触发且无满足项 → notice 锚定触发词', () => {
    const m = runCheckRules(docOf('<p>本方案涉及 9800M12 设备。</p>'), compileRules([rule]))
    expect(m).toHaveLength(1)
    expect(m[0]).toMatchObject({ notice: true, text: '9800M12' })
  })
  it('补任一满足项 → 无提示', () => {
    expect(runCheckRules(docOf('<p>涉及 9800M12 与 9800UPS。</p>'), compileRules([rule]))).toHaveLength(0)
    expect(runCheckRules(docOf('<p>涉及 9800M12，不涉及拖带。</p>'), compileRules([rule]))).toHaveLength(0)
  })
  it('多段落也能跨段落判定', () => {
    const m = runCheckRules(docOf('<p>9800M12</p><p>其它说明</p>'), compileRules([rule]))
    expect(m).toHaveLength(1)
  })
})

describe('match ids', () => {
  it('同一规则 maps 与 whitelists 在同一位置命中时 id 不同', () => {
    const rule = R({
      id: 'dup',
      maps: [{ wrong: 'S-5700', right: 'S5700' }],
      whitelists: [{ anchors: ['设备：'], values: ['S5700'], message: '不在清单' }],
    })
    const m = runCheckRules(docOf('<p>设备：S-5700</p>'), compileRules([rule]))
    expect(m).toHaveLength(2)
    expect(m[0].from).toBe(m[1].from)
    expect(m[0].to).toBe(m[1].to)
    expect(new Set(m.map((x) => x.id)).size).toBe(2)
  })
  it('同一规则多个 presence 条目 id 不同', () => {
    const rule = R({ id: 'pc2', presence: [
      { triggers: ['9800M12'], requiresAny: ['9800UPS'], message: '缺 A' },
      { triggers: ['9800M12'], requiresAny: ['9800UPS'], message: '缺 B' },
    ] })
    const m = runCheckRules(docOf('<p>9800M12</p>'), compileRules([rule]))
    expect(m).toHaveLength(2)
    expect(new Set(m.map((x) => x.id)).size).toBe(2)
  })
})
