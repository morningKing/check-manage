/**
 * Prompt-template user preferences (P2 spec §6.1): favorites and recent-use
 * timestamps are stored client-side per browser (localStorage) — the spec
 * explicitly allows a local-first scheme until the backend records useCount.
 */

const FAV_KEY = 'check-manage:tpl:favorites'
const RECENT_KEY = 'check-manage:tpl:recent'

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch { return fallback }
}

function writeJson(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* private mode etc. */ }
}

export function isFavorite(id: string): boolean {
  return readJson<string[]>(FAV_KEY, []).includes(id)
}

export function toggleFavorite(id: string): boolean {
  const favs = readJson<string[]>(FAV_KEY, [])
  const idx = favs.indexOf(id)
  if (idx >= 0) favs.splice(idx, 1)
  else favs.push(id)
  writeJson(FAV_KEY, favs)
  return idx < 0
}

export function lastUsedAt(id: string): number | null {
  return readJson<Record<string, number>>(RECENT_KEY, {})[id] ?? null
}

/** Record a template use (应用/插入时调用)。返回时间戳。 */
export function recordTemplateUse(id: string): number {
  const map = readJson<Record<string, number>>(RECENT_KEY, {})
  map[id] = Date.now()
  writeJson(RECENT_KEY, map)
  return map[id]
}

export function forgetTemplate(id: string) {
  const favs = readJson<string[]>(FAV_KEY, []).filter(x => x !== id)
  writeJson(FAV_KEY, favs)
  const map = readJson<Record<string, number>>(RECENT_KEY, {})
  if (map[id] !== undefined) { delete map[id]; writeJson(RECENT_KEY, map) }
}
