import { describe, it, expect, beforeEach } from 'vitest'
import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import {
  LOCAL_RULES, disabledLocalRuleIds, isLocalRuleEnabled, runLocalRules, setLocalRuleEnabled,
} from '../local-rules'

/** 构造单段落文档（JSON 内容：HTML 解析会折叠连续空格，程序化文本才保真） */
function docWith(text: string) {
  const editor = new Editor({
    extensions: [StarterKit],
    content: {
      type: 'doc',
      content: [{ type: 'paragraph', content: [{ type: 'text', text }] }],
    },
  })
  const doc = editor.state.doc
  editor.destroy()
  return doc
}

describe('内置规则临时屏蔽', () => {
  beforeEach(() => {
    localStorage.removeItem('check-manage:se:disabled-local-rules')
  })

  it('默认全部启用，命中双空格与其余规则', () => {
    expect(disabledLocalRuleIds()).toEqual([])
    expect(isLocalRuleEnabled('double-space')).toBe(true)
    const matches = runLocalRules(docWith('方案  说明'))
    expect(matches.map((m) => m.ruleId)).toContain('double-space')
  })

  it('屏蔽后不再命中该规则，其余规则照常', () => {
    setLocalRuleEnabled('double-space', false)
    expect(disabledLocalRuleIds()).toEqual(['double-space'])
    expect(isLocalRuleEnabled('double-space')).toBe(false)

    const matches = runLocalRules(docWith('方案  说明'))
    expect(matches.map((m) => m.ruleId)).not.toContain('double-space')
    // 屏蔽连续空格不影响同文档的其它内置规则（重复句号）
    const other = runLocalRules(docWith('完成。。'))
    expect(other.map((m) => m.ruleId)).toContain('dup-punct')
  })

  it('重新启用即恢复命中，localStorage 落盘', () => {
    setLocalRuleEnabled('double-space', false)
    setLocalRuleEnabled('double-space', true)
    expect(disabledLocalRuleIds()).toEqual([])
    expect(JSON.parse(localStorage.getItem('check-manage:se:disabled-local-rules') ?? '[]')).toEqual([])
    expect(runLocalRules(docWith('方案  说明')).map((m) => m.ruleId)).toContain('double-space')
  })

  it('LOCAL_RULES 元数据完整（UI 开关区渲染依据）', () => {
    expect(LOCAL_RULES.length).toBeGreaterThanOrEqual(6)
    for (const rule of LOCAL_RULES) {
      expect(rule.id).toBeTruthy()
      expect(rule.reason).toBeTruthy()
      expect(['info', 'warn', 'error']).toContain(rule.severity)
    }
  })
})
