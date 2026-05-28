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

/** Diagram/chart langs md-editor renders inline; never lift these into artifacts. */
const INLINE_RENDER_LANGS = new Set(['mermaid', 'echarts'])

export function isInlineRenderLang(lang: string): boolean {
  return INLINE_RENDER_LANGS.has((lang || '').trim().toLowerCase())
}

export function splitArtifacts(src: string): Segment[] {
  const segs: Segment[] = []
  let last = 0
  let m: RegExpExecArray | null
  FENCE.lastIndex = 0
  while ((m = FENCE.exec(src))) {
    const lang = (m[1] || '').trim()
    const code = m[2].replace(/\n+$/, '')
    if (isInlineRenderLang(lang)) continue // mermaid/echarts render inline via md-editor
    if (!isArtifact(code)) continue // leave small snippets inline in the prose
    if (m.index > last) segs.push({ type: 'text', text: src.slice(last, m.index) })
    segs.push({ type: 'code', lang, code })
    last = m.index + m[0].length
  }
  if (last < src.length) segs.push({ type: 'text', text: src.slice(last) })
  if (!segs.length) segs.push({ type: 'text', text: src })
  return segs
}

const LANG_EXT: Record<string, string> = {
  python: 'py', py: 'py', javascript: 'js', js: 'js', typescript: 'ts', ts: 'ts',
  tsx: 'tsx', jsx: 'jsx', bash: 'sh', sh: 'sh', shell: 'sh', json: 'json',
  yaml: 'yaml', yml: 'yml', sql: 'sql', html: 'html', svg: 'svg', css: 'css', xml: 'xml',
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

/** html/svg can be rendered live in a sandboxed iframe. */
export function isRenderableLang(lang: string): boolean {
  const l = lang.toLowerCase()
  return l === 'html' || l === 'svg'
}

/** python artifacts can be executed server-side to produce a result file. */
export function isRunnableLang(lang: string): boolean {
  const l = lang.toLowerCase()
  return l === 'python' || l === 'py' || l.endsWith('.py')
}

/**
 * Infer a better language than the fence label. Models (e.g. MiMo) often mislabel
 * code (Python tagged as bash) or omit the label; sniff from the content.
 */
export function sniffLang(lang: string, code: string): string {
  const l = (lang || '').toLowerCase().trim()
  if (l.includes('.')) return l // looks like a filename, trust it
  const head = code.slice(0, 400)
  const looksSvg = /^\s*<svg[\s>]/i.test(head)
  const looksHtml = /^\s*<!doctype html/i.test(head) || /^\s*<html[\s>]/i.test(head)
    || /<\/(div|body|head|p|span|table|h[1-6]|ul|section)>/i.test(code)
  const looksPy = /^\s*(import \w|from [\w.]+ import |def \w+\s*\(|class \w+\s*[:(]|print\()/m.test(code)
  const looksSh = /^#!.*\b(bash|sh)\b/.test(head) || /^\s*(echo |cd |grep |awk |sudo |export )/m.test(code)
  if (looksSvg) return 'svg'
  if (looksHtml) return 'html'
  const generic = !l || l === 'text' || l === 'code' || l === 'plaintext'
  if (generic) {
    if (looksPy) return 'python'
    if (/\bfunction\b|=>|\bconst \w|console\.|document\./.test(code)) return 'javascript'
    if (/^\s*[{[]/.test(head) && /[}\]]\s*$/.test(code.trim())) return 'json'
    if (/\bSELECT\b|\bCREATE TABLE\b|\bINSERT INTO\b/i.test(code)) return 'sql'
    if (looksSh) return 'bash'
    return l || 'text'
  }
  // explicit but likely wrong: Python mislabeled as a shell
  if ((l === 'bash' || l === 'sh' || l === 'shell') && looksPy && !looksSh) return 'python'
  return l
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
