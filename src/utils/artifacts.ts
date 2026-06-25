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

// Langs that should NEVER be lifted into an artifact card — they read better
// inline in the chat flow than as a separate file bubble. Covers prose, shell
// commands, and SVG (which MarkdownView pre-processes into an inline image so
// the user sees the picture in place instead of a "click to preview" bubble).
const PROSE_LANGS = new Set([
  '', 'text', 'txt', 'plaintext', 'plain',
  'bash', 'sh', 'shell', 'zsh', 'console',
  'svg',
])

/** A fenced block becomes an artifact when it's big enough to be worth lifting.
 *  Thresholds are intentionally permissive — small snippets read better inline. */
function isArtifact(lang: string, code: string): boolean {
  if (PROSE_LANGS.has((lang || '').trim().toLowerCase())) return false
  const lines = code.replace(/\n+$/, '').split('\n').length
  return lines >= 8 || code.length >= 320
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
    // Leave mermaid/echarts in place: by NOT advancing `last`, the fenced block
    // stays inside the surrounding text segment and md-editor renders it inline
    // as a diagram/chart. (Advancing `last` here would drop the diagram.)
    if (isInlineRenderLang(lang)) continue
    if (!isArtifact(lang, code)) continue // leave small snippets / prose blocks inline
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

const IMAGE_EXTS = new Set(['svg', 'png', 'jpg', 'jpeg', 'gif', 'webp'])

/** True if a filename looks like an inline-renderable image (by extension). */
export function isImageFile(name: string): boolean {
  const i = name.lastIndexOf('.')
  if (i < 0) return false
  return IMAGE_EXTS.has(name.slice(i + 1).trim().toLowerCase())
}

/** python artifacts can be executed server-side to produce a result file. */
export function isRunnableLang(lang: string): boolean {
  const l = lang.toLowerCase()
  return l === 'python' || l === 'py' || l.endsWith('.py')
}

/**
 * Infer a better language than the fence label. Some LLMs mislabel
 * code (e.g. Python tagged as bash) or omit the label; sniff from the content.
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

export interface FileGroup<T> { dir: string; label: string; files: T[] }

/**
 * Group files by their parent directory so the 产出文件 panel can render one
 * collapsible section per directory (instead of a long flat list). Root-level
 * files (no `/` in the path) go under an empty-dir group labelled 根目录.
 * Groups are ordered root-first then alphabetically; files within a group are
 * path-sorted. Pure + deterministic so it's unit-testable.
 */
export function groupFilesByDir<T extends { path: string }>(files: T[]): FileGroup<T>[] {
  const map = new Map<string, T[]>()
  for (const f of files) {
    const p = f.path.replace(/\\/g, '/')
    const i = p.lastIndexOf('/')
    const dir = i >= 0 ? p.slice(0, i) : ''
    const bucket = map.get(dir)
    if (bucket) bucket.push(f)
    else map.set(dir, [f])
  }
  const dirs = [...map.keys()].sort((a, b) => {
    if (a === b) return 0
    if (a === '') return -1
    if (b === '') return 1
    return a < b ? -1 : 1
  })
  return dirs.map((dir) => ({
    dir,
    label: dir === '' ? '根目录' : dir + '/',
    files: map.get(dir)!.slice().sort((x, y) =>
      x.path < y.path ? -1 : x.path > y.path ? 1 : 0),
  }))
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
