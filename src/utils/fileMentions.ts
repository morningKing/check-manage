/**
 * @-mention file paths (F2): palette token detection, fuzzy file filtering and
 * message segment splitting for clickable file chips.
 *
 * Agent mentions (`@<name>`, `[A-Za-z0-9_-]+`) are handled by agentMentions.ts;
 * this module broadens the @-token to path characters (`/`, `.`, CJK) so a file
 * reference like `@src/main.ts` is treated as one token, and turns references
 * that resolve to a real workspace file into chips.
 */

/** Minimal file shape (structurally compatible with AiFile). */
export interface AiFileLike {
  name: string
  path: string
  dir?: string
  size?: number
}

// A character that may appear inside an @<path> token: word chars, path
// separators, dots, hyphen and CJK. Whitespace/`@` terminate the token.
const PATH_PART = /[A-Za-z0-9_./\u4e00-\u9fa5-]/

export interface AtToken {
  /** Text between `@` and the cursor (the palette filter query). */
  query: string
  /** Index of the `@`. */
  start: number
  /** Cursor index (end of the token). */
  end: number
}

/**
 * Find the active `@<path>` token immediately left of `cursor`.
 * `@` must sit at line start or after whitespace (so emails aren't matches);
 * the run between `@` and cursor may contain path characters.
 */
export function activeAtToken(text: string, cursor: number): AtToken | null {
  let i = cursor - 1
  while (i >= 0 && PATH_PART.test(text[i])) i--
  if (i < 0 || text[i] !== '@') return null
  if (i > 0 && !/\s/.test(text[i - 1])) return null
  return { query: text.slice(i + 1, cursor), start: i, end: cursor }
}

/**
 * Substring fuzzy filter over the session file list. Basename prefix matches
 * rank highest, then path prefix, then basename/path substring. Caps at `limit`
 * (20, per spec) to keep the palette compact.
 */
export function filterFileItems<T extends AiFileLike>(files: T[], query: string, limit = 20): T[] {
  const q = (query || '').toLowerCase().replace(/^@/, '').trim()
  const scored: { f: T; score: number }[] = []
  for (const f of files) {
    const p = f.path.toLowerCase()
    const base = f.name.toLowerCase()
    let score = -1
    if (!q) score = 0
    else if (base.startsWith(q)) score = 1
    else if (p.startsWith(q)) score = 2
    else if (base.includes(q)) score = 3
    else if (p.includes(q)) score = 4
    if (score >= 0) scored.push({ f, score })
  }
  scored.sort((a, b) => a.score - b.score || a.f.path.localeCompare(b.f.path))
  return scored.slice(0, limit).map((s) => s.f)
}

export type MentionSegment =
  | { type: 'text'; text: string }
  | { type: 'file'; path: string }

// `@` at start/whitespace, then a path-character run (mirrors the backend regex).
const MENTION_RE = /(^|\s)@([A-Za-z0-9_./\u4e00-\u9fa5-]+)/g

/**
 * Split message text into text/file segments for rendering. A token becomes a
 * `file` chip only when it matches a known workspace path in `knownPaths` —
 * agent names, emails and paths that don't exist stay as plain text.
 */
export function splitMentionSegments(text: string, knownPaths: Set<string>): MentionSegment[] {
  const segments: MentionSegment[] = []
  let last = 0
  let m: RegExpExecArray | null
  MENTION_RE.lastIndex = 0
  while ((m = MENTION_RE.exec(text))) {
    const token = m[2]
    const path = token.replace(/\.+$/, '') // tolerate "@file." at sentence end
    if (!knownPaths.has(path)) continue
    const at = m.index + m[1].length // index of the '@'
    if (at > last) segments.push({ type: 'text', text: text.slice(last, at) })
    segments.push({ type: 'file', path })
    last = at + 1 + token.length // after '@' + full (untrimmed) token
  }
  if (last < text.length) segments.push({ type: 'text', text: text.slice(last) })
  return segments
}
