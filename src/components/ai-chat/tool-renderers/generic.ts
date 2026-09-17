/**
 * Shared helpers for tool-call summaries (P2 spec §5.5–§5.8).
 * All pure functions — deterministic, no model calls, no side effects.
 */

/** Sensitive keys masked in raw details (P2 spec §5.8). Matched case-insensitively. */
export const SENSITIVE_KEYS = [
  'token', 'access_token', 'accesstoken', 'apikey', 'api_key',
  'password', 'secret', 'authorization', 'cookie',
]

export function isSensitiveKey(key: string, extra: string[] = []): boolean {
  const k = key.toLowerCase().replace(/^headers\./, '')
  return [...SENSITIVE_KEYS, ...extra.map(e => e.toLowerCase())]
    .some(s => k === s || k.endsWith('.' + s) || k.includes(s))
}

/** `sk-****abcd` style masking — keeps a short head/tail, masks the middle. */
export function maskSensitiveValue(v: unknown): string {
  const s = typeof v === 'string' ? v : JSON.stringify(v) ?? ''
  if (s.length <= 8) return '****'
  return `${s.slice(0, 2)}****${s.slice(-4)}`
}

/** Deep-clone a JSON value with sensitive fields masked. Non-JSON input passes through. */
export function redact(value: unknown, extraKeys: string[] = []): unknown {
  if (Array.isArray(value)) return value.map(v => redact(v, extraKeys))
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = isSensitiveKey(k, extraKeys) ? maskSensitiveValue(v) : redact(v, extraKeys)
    }
    return out
  }
  return value
}

/** Stable, safe JSON text of a redacted value; falls back to String(). */
export function stringifyRedacted(value: unknown, extraKeys: string[] = []): string {
  try {
    return JSON.stringify(redact(value, extraKeys), null, 2) ?? ''
  } catch {
    return String(value)
  }
}

/** First line of a string, hard-capped at `max` chars (P2 spec §5.6). */
export function firstLine(s: string, max = 120): string {
  const line = (s || '').split(/\r?\n/, 1)[0] ?? ''
  return line.length > max ? line.slice(0, max - 1) + '…' : line
}

/** Basename only — summaries never expose full internal paths (§5.6). */
export function baseName(p: string): string {
  return (p || '').replace(/\\/g, '/').split('/').filter(Boolean).pop() || p
}

/**
 * Normalize a tool result into a JSON value. OpenCode frequently hands the
 * output over as a JSON-encoded string — try to unwrap one level.
 */
export function parseResult(result: unknown): unknown {
  if (typeof result !== 'string') return result
  const t = result.trim()
  if (!t || (!t.startsWith('{') && !t.startsWith('['))) return result
  try { return JSON.parse(t) } catch { return result }
}

const FILE_PATH_KEYS = ['path', 'file', 'filepath', 'file_path', 'output', 'artifact', 'filename', 'file_name']

/** Pick the first string value under a file-ish key. */
export function findFilePath(obj: Record<string, unknown>): string | undefined {
  for (const k of FILE_PATH_KEYS) {
    const v = obj[k]
    if (typeof v === 'string' && v) return v
  }
  return undefined
}

/** Look for an error-shaped object (`{error}` / `{isError:true,...}`). */
export function findError(result: unknown): string | undefined {
  if (!result || typeof result !== 'object') return undefined
  const o = result as Record<string, unknown>
  if (typeof o.error === 'string' && o.error) return o.error
  if (o.error && typeof o.error === 'object') {
    const e = o.error as Record<string, unknown>
    if (typeof e.message === 'string') return e.message
  }
  if (o.isError === true) {
    for (const k of ['message', 'content', 'text', 'detail']) {
      if (typeof o[k] === 'string' && o[k]) return o[k] as string
    }
  }
  return undefined
}

export function truncate(s: string, max = 120): string {
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}
