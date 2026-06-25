/** Format helpers for AI message execution metadata (duration / tokens / cost). */

export function formatDuration(ms?: number | null): string {
  if (ms == null || ms < 0) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const rs = Math.round(s % 60)
  return `${m}m${rs}s`
}

export function formatTokens(n?: number | null): string {
  if (n == null) return ''
  if (n < 1000) return String(n)
  return `${(n / 1000).toFixed(1)}k`
}

export function formatCost(c?: number | null): string {
  if (c == null) return ''
  if (c === 0) return '$0'
  if (c < 0.01) return `$${c.toFixed(4)}`
  return `$${c.toFixed(2)}`
}

export interface AiMetaLike {
  durationMs?: number
  tokensInput?: number
  tokensOutput?: number
  cost?: number
}

/** A compact one-line summary, e.g. "⏱ 4.4s · ↑18.9k ↓159 · $0". Empty when
 *  there's nothing meaningful to show. */
export function summarizeMeta(meta?: AiMetaLike | null): string {
  if (!meta) return ''
  const parts: string[] = []
  const d = formatDuration(meta.durationMs)
  if (d) parts.push(`⏱ ${d}`)
  const tin = formatTokens(meta.tokensInput)
  const tout = formatTokens(meta.tokensOutput)
  if (tin || tout) parts.push(`↑${tin || 0} ↓${tout || 0}`)
  const c = formatCost(meta.cost)
  if (c) parts.push(c)
  return parts.join(' · ')
}
