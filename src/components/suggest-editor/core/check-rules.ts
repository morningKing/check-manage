/**
 * 检查规则本地引擎：映射 / 正则提取校验 / 锚定白名单 / 文档级条件完整性。
 * 纯函数：compileRules 一次性编译（挂载/规则变更时），runCheckRules 每次编辑调用。
 * 约束：文本内匹配限单个文本节点；presence 用全文纯文本；全文 >500k 字符跳过 presence。
 */
import type { Node as ProseMirrorNode } from '@tiptap/pm/model'

export type CheckSeverity = 'info' | 'warn' | 'error'

/** 命中机制：UI 的 data-mechanism / E2E 精确定位用 */
export type CheckMechanism = 'maps' | 'patterns' | 'whitelists' | 'presence'

export interface LocalCheckRuleInput {
  id: string
  name: string
  enabled: boolean
  severity: CheckSeverity
  aiPrompt?: string
  maps?: { wrong: string; right: string; caseSensitive?: boolean }[]
  patterns?: { extract: string; valid: string; message: string; flags?: string }[]
  whitelists?: { anchors: string[]; values: string[]; valuePattern?: string; message: string }[]
  presence?: { triggers: string[]; requiresAny: string[]; message: string }[]
}

export interface RuleMatch {
  id: string
  ruleId: string
  ruleName: string
  mechanism: CheckMechanism
  severity: CheckSeverity
  reason: string
  from: number
  to: number
  text: string
  replacement: string | null
  notice: boolean
}

export interface CompiledRules {
  maps: Array<{
    rule: LocalCheckRuleInput
    regex: RegExp
    byLower: Map<string, { wrong: string; right: string }>
  }>
  patterns: Array<{ rule: LocalCheckRuleInput; extract: RegExp; valid: RegExp; message: string }>
  whitelists: Array<{ rule: LocalCheckRuleInput; anchors: string[]; values: Set<string>;
    valueRegex: RegExp; message: string }>
  presence: Array<{ rule: LocalCheckRuleInput; triggers: string[]; requiresAny: string[];
    message: string }>
}

/** 空编译结果：无规则时的默认值（Task 5 decoration 层直接复用） */
export const EMPTY_COMPILED_RULES: CompiledRules = {
  maps: [],
  patterns: [],
  whitelists: [],
  presence: [],
}

const PRESENCE_MAX_CHARS = 500_000
const DEFAULT_VALUE_PATTERN = '[A-Za-z0-9_\\-./]+'
/** 锚点后的多个值：用列表分隔符（中英文标点/连接词）继续逐个校验，而不是只看第一个 */
const WHITELIST_SEPARATORS = new Set(Array.from('、,，;；/|&和及与或'))
/** 全文超限跳过 presence 的一次性告警标记（防每次编辑刷屏） */
let presenceSkipWarned = false

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function compileRules(rules: LocalCheckRuleInput[]): CompiledRules {
  const compiled: CompiledRules = { maps: [], patterns: [], whitelists: [], presence: [] }
  for (const rule of rules) {
    if (!rule.enabled) continue
    if (rule.maps?.length) {
      // 分组：大小写敏感/不敏感分别合并为 alternation，一次扫描
      for (const caseSensitive of [false, true]) {
        const items = rule.maps.filter((m) => (m.caseSensitive ?? false) === caseSensitive)
        if (!items.length) continue
        const byLower = new Map(items.map((m) => [m.wrong.toLowerCase(), m]))
        try {
          const source = items.map((m) => escapeRegex(m.wrong)).join('|')
          compiled.maps.push({ rule, regex: new RegExp(source, caseSensitive ? 'g' : 'gi'), byLower })
        } catch (err) {
          console.warn('[suggest-editor] 规则编译失败，已跳过：', rule.id, err)
        }
      }
    }
    for (const p of rule.patterns ?? []) {
      try {
        // flags 由上游自由填写：去掉会与必需的 g 冲突的 g/y，避免 gg/yg 非法标志吞掉整条规则
        const flags = (p.flags ?? '').replace(/[gy]/g, '') + 'g'
        compiled.patterns.push({
          rule, extract: new RegExp(p.extract, flags),
          valid: new RegExp(p.valid), message: p.message,
        })
      } catch (err) {
        console.warn('[suggest-editor] 规则编译失败，已跳过：', rule.id, err)
      }
    }
    for (const w of rule.whitelists ?? []) {
      try {
        compiled.whitelists.push({
          rule, anchors: w.anchors, values: new Set(w.values),
          valueRegex: new RegExp(w.valuePattern ?? DEFAULT_VALUE_PATTERN, 'y'), message: w.message,
        })
      } catch (err) {
        console.warn('[suggest-editor] 规则编译失败，已跳过：', rule.id, err)
      }
    }
    for (const pr of rule.presence ?? []) {
      compiled.presence.push({ rule, triggers: pr.triggers, requiresAny: pr.requiresAny, message: pr.message })
    }
  }
  return compiled
}

type TextMechanism = 'maps' | 'patterns' | 'whitelists'

function matchInTextNode(
  node: ProseMirrorNode, pos: number, compiled: CompiledRules, out: RuleMatch[],
): void {
  const text = node.text ?? ''
  if (!text) return
  const push = (rule: LocalCheckRuleInput, mechanism: TextMechanism, entryIndex: number,
                from: number, to: number, replacement: string | null,
                message: string, notice: boolean): void => {
    const globalFrom = pos + from
    const globalTo = pos + to
    out.push({
      id: `rule:${rule.id}:${mechanism}:${entryIndex}:${globalFrom}:${globalTo}`,
      ruleId: rule.id, ruleName: rule.name, mechanism,
      severity: rule.severity, reason: `[${rule.name}] ${message}`,
      from: globalFrom, to: globalTo, text: text.slice(from, to),
      replacement, notice,
    })
  }
  for (let i = 0; i < compiled.maps.length; i += 1) {
    const entry = compiled.maps[i]
    entry.regex.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = entry.regex.exec(text)) !== null) {
      const hit = entry.byLower.get(m[0].toLowerCase())
      if (hit) push(entry.rule, 'maps', i, m.index, m.index + m[0].length, hit.right, '请使用规范写法', false)
      if (m[0].length === 0) entry.regex.lastIndex += 1
    }
  }
  for (let i = 0; i < compiled.patterns.length; i += 1) {
    const entry = compiled.patterns[i]
    entry.extract.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = entry.extract.exec(text)) !== null) {
      if (!entry.valid.test(m[0])) {
        push(entry.rule, 'patterns', i, m.index, m.index + m[0].length, null, entry.message, true)
      }
      if (m[0].length === 0) entry.extract.lastIndex += 1
    }
  }
  for (let i = 0; i < compiled.whitelists.length; i += 1) {
    const entry = compiled.whitelists[i]
    for (const anchor of entry.anchors) {
      let at = text.indexOf(anchor)
      while (at !== -1) {
        let cursor = at + anchor.length
        while (cursor < text.length && (text[cursor] === ' ' || text[cursor] === '\t')) cursor += 1
        // 一个锚点后可能是列表（如「设备类型：9800M12；5000M12」）：逐个值检查，
        // 只允许用列表分隔符继续，遇到其它文字/标点就停，避免把后文误当值。
        while (cursor < text.length) {
          entry.valueRegex.lastIndex = cursor
          const m = entry.valueRegex.exec(text)
          if (!m || m[0].length === 0) break
          if (!entry.values.has(m[0])) {
            push(entry.rule, 'whitelists', i, m.index, m.index + m[0].length, null, entry.message, true)
          }
          cursor = m.index + m[0].length
          let next = cursor
          while (next < text.length && (text[next] === ' ' || text[next] === '\t')) next += 1
          if (next >= text.length || !WHITELIST_SEPARATORS.has(text[next])) break
          cursor = next + 1
          while (cursor < text.length && (text[cursor] === ' ' || text[cursor] === '\t')) cursor += 1
        }
        at = text.indexOf(anchor, at + anchor.length)
      }
    }
  }
}

function findFirstOccurrence(
  doc: ProseMirrorNode, term: string,
): { from: number; to: number } | null {
  const lower = term.toLowerCase()
  let found: { from: number; to: number } | null = null
  doc.descendants((node, pos) => {
    if (found || !node.isText || !node.text) return !found
    const at = node.text.toLowerCase().indexOf(lower)
    if (at >= 0) found = { from: pos + at, to: pos + at + term.length }
    return !found
  })
  return found
}

export function runCheckRules(doc: ProseMirrorNode, compiled: CompiledRules): RuleMatch[] {
  const matches: RuleMatch[] = []
  doc.descendants((node, pos) => {
    if (node.isText) matchInTextNode(node, pos, compiled, matches)
    return true
  })
  if (!compiled.presence.length) return matches
  const fullText = doc.textBetween(0, doc.content.size, '\n')
  if (fullText.length > PRESENCE_MAX_CHARS) {
    if (!presenceSkipWarned) {
      presenceSkipWarned = true
      console.warn(`[suggest-editor] 文档超过 ${PRESENCE_MAX_CHARS} 字符，已跳过条件完整性（presence）检查`)
    }
    return matches
  }
  const lower = fullText.toLowerCase()
  for (let i = 0; i < compiled.presence.length; i += 1) {
    const entry = compiled.presence[i]
    const triggered = entry.triggers.some((t) => lower.includes(t.toLowerCase()))
    if (!triggered) continue
    const satisfied = entry.requiresAny.some((t) => lower.includes(t.toLowerCase()))
    if (satisfied) continue
    const trigger = entry.triggers.find((t) => lower.includes(t.toLowerCase())) ?? entry.triggers[0]
    const spot = findFirstOccurrence(doc, trigger)
    if (!spot) continue
    matches.push({
      id: `rule:${entry.rule.id}:presence:${i}`, ruleId: entry.rule.id, ruleName: entry.rule.name,
      mechanism: 'presence',
      severity: entry.rule.severity, reason: `[${entry.rule.name}] ${entry.message}`,
      from: spot.from, to: spot.to, text: trigger, replacement: null, notice: true,
    })
  }
  return matches
}
