import type { AgentMention } from '@/api/aiChat'

/**
 * 找光标左侧正在输入的 @token：@ 必须位于行首或空白后，@ 与光标间只有 [\w-]。
 * 返回 { query, start(@位置), end(光标) }，无则 null。
 */
export function activeMentionToken(
  text: string,
  cursor: number,
): { query: string; start: number; end: number } | null {
  let i = cursor - 1
  while (i >= 0 && /[A-Za-z0-9_-]/.test(text[i])) i--
  if (i < 0 || text[i] !== '@') return null
  if (i > 0 && !/\s/.test(text[i - 1])) return null
  return { query: text.slice(i + 1, cursor), start: i, end: cursor }
}

/**
 * 扫描文本所有 @<name>（行首或空白后），保留 name ∈ knownNames，记录 value 与偏移 [start,end)。
 */
export function parseAgentMentions(text: string, knownNames: Set<string>): AgentMention[] {
  const out: AgentMention[] = []
  const re = /(^|\s)@([A-Za-z0-9_-]+)/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    const name = m[2]
    if (!knownNames.has(name)) continue
    const start = m.index + m[1].length
    out.push({ name, value: '@' + name, start, end: start + name.length + 1 })
  }
  return out
}
