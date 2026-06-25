/**
 * Parse an OpenCode todo tool-call (todowrite / todoread) into a checklist the
 * UI can render, so the user can see the agent's plan and which step it's on.
 *
 * The agent's todo items live in the tool INPUT for `todowrite` (the agent
 * writes the list) and in the RESULT for `todoread`; we look in both. Each item
 * is `{ content, status }` where status normalises to one of four states.
 */

export type TodoStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled'

export interface TodoItem {
  content: string
  status: TodoStatus
}

function normalizeStatus(s: unknown): TodoStatus {
  const v = (s ?? '').toString().toLowerCase().replace(/[\s-]/g, '_')
  if (v === 'in_progress' || v === 'inprogress' || v === 'active') return 'in_progress'
  if (v === 'completed' || v === 'complete' || v === 'done') return 'completed'
  if (v === 'cancelled' || v === 'canceled') return 'cancelled'
  return 'pending'
}

function extractTodos(v: unknown): TodoItem[] | null {
  let obj: unknown = v
  if (typeof obj === 'string') {
    try { obj = JSON.parse(obj) } catch { return null }
  }
  const arr = Array.isArray(obj)
    ? obj
    : (obj && typeof obj === 'object' ? (obj as Record<string, unknown>).todos : null)
  if (!Array.isArray(arr)) return null
  const out: TodoItem[] = []
  for (const t of arr) {
    if (!t || typeof t !== 'object') continue
    const rec = t as Record<string, unknown>
    const content = (rec.content ?? rec.text ?? rec.title ?? '').toString().trim()
    if (!content) continue
    out.push({ content, status: normalizeStatus(rec.status) })
  }
  return out.length ? out : null
}

/**
 * Returns the todo checklist if `part` is a todo tool-call with usable items,
 * else null (so the caller falls back to the generic tool bubble).
 */
export function parseTodos(
  part: { name?: string; input?: unknown; result?: unknown } | null | undefined,
): TodoItem[] | null {
  if (!part?.name || !/todo/i.test(part.name)) return null
  // todowrite carries the list in `input`; todoread in `result`. Prefer input.
  return extractTodos(part.input) ?? extractTodos(part.result)
}

/** Count completed / total for a progress label. */
export function todoProgress(todos: TodoItem[]): { done: number; total: number } {
  return {
    done: todos.filter((t) => t.status === 'completed').length,
    total: todos.length,
  }
}
