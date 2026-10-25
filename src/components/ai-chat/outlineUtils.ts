/**
 * 会话大纲纯逻辑（spec 2026-10-25-session-outline §6）：与组件/视图解耦，
 * 便于单测。previewOfParts 与后端 _turn_preview 同规则（text 优先、纯文件
 * 给文件名摘要）。
 */
import type { AiMessage, AiContentPart } from '@/api/aiChat'

/** 视口顶部覆盖到 messages[upToIdx] 时，向前找最近一条已落库用户回合的 seq。 */
export function nearestUserTurnSeq(messages: AiMessage[], upToIdx: number): number | null {
  for (let i = Math.min(upToIdx, messages.length - 1); i >= 0; i--) {
    const m = messages[i]
    if (m && m.role === 'user' && m.seq != null) return m.seq
  }
  return null
}

/** 最后一条已落库用户回合的 seq（scroll-spy 贴底特例：视口顶仍在倒数
 *  第二轮内时，用户实际在读最后一轮 → 底部高亮最后一轮；无用户回合返回 null）。 */
export function lastUserTurnSeq(messages: AiMessage[]): number | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m && m.role === 'user' && m.seq != null) return m.seq
  }
  return null
}

/** 用户消息 parts 的单行预览（≤160 字符）。 */
export function previewOfParts(parts: AiContentPart[]): string {
  for (const p of parts) {
    if (p.type === 'text' && p.text.trim()) {
      return p.text.replace(/\s+/g, ' ').trim().slice(0, 160)
    }
  }
  const names = parts
    .filter((p): p is Extract<AiContentPart, { type: 'file' }> => p.type === 'file')
    .map(p => p.name)
    .filter(Boolean)
  if (!names.length) return '（无文本）'
  const shown = names.slice(0, 3).join('、')
  return '📎 ' + shown + (names.length > 3 ? ` 等 ${names.length} 个` : '')
}
