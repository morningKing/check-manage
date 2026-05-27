/**
 * Artifact extraction: split assistant markdown into prose + "artifact" blocks.
 *
 * Mimics Claude's behaviour of lifting substantial generated content (code or
 * documents) out of the chat flow into a previewable/downloadable artifact.
 * Small inline snippets are left in the prose (rendered as normal code).
 */

export interface TextSegment { type: 'text'; text: string }
export interface CodeSegment { type: 'code'; lang: string; code: string }
export type Segment = TextSegment | CodeSegment

const FENCE = /```([^\n`]*)\n([\s\S]*?)```/g

/** A fenced block becomes an artifact when it's big enough to be worth lifting. */
function isArtifact(code: string): boolean {
  const lines = code.replace(/\n+$/, '').split('\n').length
  return lines >= 6 || code.length >= 240
}

export function splitArtifacts(src: string): Segment[] {
  const segs: Segment[] = []
  let last = 0
  let m: RegExpExecArray | null
  FENCE.lastIndex = 0
  while ((m = FENCE.exec(src))) {
    const code = m[2].replace(/\n+$/, '')
    if (!isArtifact(code)) continue // leave small snippets inline in the prose
    if (m.index > last) segs.push({ type: 'text', text: src.slice(last, m.index) })
    segs.push({ type: 'code', lang: (m[1] || '').trim(), code })
    last = m.index + m[0].length
  }
  if (last < src.length) segs.push({ type: 'text', text: src.slice(last) })
  if (!segs.length) segs.push({ type: 'text', text: src })
  return segs
}

const LANG_EXT: Record<string, string> = {
  python: 'py', py: 'py', javascript: 'js', js: 'js', typescript: 'ts', ts: 'ts',
  tsx: 'tsx', jsx: 'jsx', bash: 'sh', sh: 'sh', shell: 'sh', json: 'json',
  yaml: 'yaml', yml: 'yml', sql: 'sql', html: 'html', css: 'css', xml: 'xml',
  markdown: 'md', md: 'md', go: 'go', java: 'java', rust: 'rs', c: 'c', cpp: 'cpp',
  text: 'txt', '': 'txt',
}

/** A fence info string may itself be a filename (contains a dot). */
export function artifactFilename(lang: string, index: number): string {
  if (lang && lang.includes('.')) return lang
  const ext = LANG_EXT[lang.toLowerCase()] ?? 'txt'
  return `artifact-${index + 1}.${ext}`
}

export function artifactLabel(lang: string, index: number): string {
  if (lang && lang.includes('.')) return lang
  return `${(lang || 'text').toUpperCase()} 片段 ${index + 1}`
}

export function isMarkdownLang(lang: string): boolean {
  const l = lang.toLowerCase()
  return l === 'md' || l === 'markdown' || l.endsWith('.md')
}

export function downloadText(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
