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
