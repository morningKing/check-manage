import type { Node as ProseMirrorNode } from "@tiptap/pm/model"

export type Severity = "info" | "warn" | "error"

export interface LocalRule {
  id: string
  severity: Severity
  reason: string
  /** 在单个文本节点内匹配（跨节点/跨格式的规则不在此层） */
  pattern: RegExp
  /** 支持 $1 这类捕获组引用，接受建议时会解析成真实文本 */
  replacement: string
}

/**
 * 本地毫秒级规则层：机械可判定、零网络、零 token 成本。
 * 风格、语义类判断交给服务端的 AI 层（opencode ACP），两层在 UI 上合并渲染并去重。
 * 想接入 Harper 等 WASM 语法检查时，只需把它的命中结果适配成 LocalMatch 即可。
 */
export const LOCAL_RULES: LocalRule[] = [
  { id: "double-space", severity: "error", reason: "连续空格", pattern: /[ \u3000]{2,}/, replacement: " " },
  {
    id: "space-before-punct",
    severity: "warn",
    reason: "标点前多余空格",
    pattern: /[ \u3000]+([，。！？；：、])/,
    replacement: "$1",
  },
  { id: "dup-punct", severity: "error", reason: "重复标点", pattern: /([，。！？；：])\1+/, replacement: "$1" },
  { id: "dup-particle", severity: "error", reason: "重复助词「的的」", pattern: /的的/, replacement: "的" },
  {
    id: "halfwidth-comma",
    severity: "info",
    reason: "中文之间的半角逗号",
    pattern: /([\u4e00-\u9fa5]),([\u4e00-\u9fa5])/,
    replacement: "$1，$2",
  },
  { id: "ascii-ellipsis", severity: "info", reason: "省略号应为「……」", pattern: /\.{3,}/, replacement: "……" },
]

export interface LocalMatch {
  id: string
  ruleId: string
  severity: Severity
  reason: string
  /** ProseMirror 位置 */
  from: number
  to: number
  text: string
  replacement: string
}

/** 在 PM 文档的每个文本节点内跑规则；文本节点内匹配 → 位置天然就是 PM 坐标 */
export function runLocalRules(doc: ProseMirrorNode): LocalMatch[] {
  const matches: LocalMatch[] = []

  doc.descendants((node, pos) => {
    if (!node.isText || !node.text) return true
    const text = node.text

    for (const rule of LOCAL_RULES) {
      const pattern = new RegExp(rule.pattern.source, "g")
      pattern.lastIndex = 0
      let match: RegExpExecArray | null
      while ((match = pattern.exec(text)) !== null) {
        if (match[0].length === 0) {
          pattern.lastIndex += 1
          continue
        }
        const from = pos + match.index
        matches.push({
          id: `${rule.id}:${from}`,
          ruleId: rule.id,
          severity: rule.severity,
          reason: rule.reason,
          from,
          to: from + match[0].length,
          text: match[0],
          replacement: match[0].replace(new RegExp(rule.pattern.source, "g"), rule.replacement),
        })
      }
    }
    return true
  })

  return matches
}
