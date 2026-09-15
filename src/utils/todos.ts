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

// ---------------------------------------------------------------------------
// 执行轨迹锚点(todo → 会话上下文定位)
//
// agent 每次状态变化都会重新调 todowrite 写**全量清单**,每次调用都是消息流里
// 的一个 tool part。把所有 todowrite 快照按消息顺序收集并做状态 diff,即可还原
// 每个步骤「何时开始执行 / 何时完成」在会话中的消息位置——todo 面板由此获得
// 「定位到上下文」的锚点与步骤耗时,而不需要任何后端新数据。
// ---------------------------------------------------------------------------

/** buildTodoTrace 的输入:只需要 role / content / createdAt。 */
export interface TodoTraceMsg {
  role: string
  content: Array<Record<string, unknown> | null | undefined> | null | undefined
  createdAt?: string
}

export interface TodoTraceItem extends TodoItem {
  /** 首次进入 in_progress 的消息下标(会话内的定位锚点) */
  startMsgIdx: number | null
  /** 进入 completed/cancelled 的消息下标 */
  endMsgIdx: number | null
  /** 锚点消息的 createdAt 毫秒时间戳(消息缺时间戳时为 null) */
  startAt: number | null
  endAt: number | null
}

function msgTimeMs(m: TodoTraceMsg, fallback: number | null): number | null {
  if (!m?.createdAt) return fallback
  const t = Date.parse(m.createdAt)
  return Number.isNaN(t) ? fallback : t
}

/**
 * 扫描消息流里的全部 todowrite 快照,按顺序做状态 diff,生成带执行锚点的轨迹。
 * 步骤按 content 字符串匹配跨快照对齐(agent 改写步骤文本会视为新步骤——一期
 * 从简);耗时的基准是锚点所在消息的 createdAt。
 */
export function buildTodoTrace(msgs: TodoTraceMsg[]): TodoTraceItem[] {
  const trace: TodoTraceItem[] = []
  const byContent = new Map<string, TodoTraceItem>()

  msgs.forEach((m, msgIdx) => {
    if (!m || m.role !== 'assistant' || !Array.isArray(m.content)) return
    const at = msgTimeMs(m, null)
    for (const part of m.content) {
      const todos = parseTodos(
        part as { name?: string; input?: unknown; result?: unknown } | null | undefined,
      )
      if (!todos) continue
      for (const t of todos) {
        const key = t.content
        let item = byContent.get(key)
        if (!item) {
          item = { ...t, startMsgIdx: null, endMsgIdx: null, startAt: null, endAt: null }
          byContent.set(key, item)
          trace.push(item)
        }
        item.status = t.status
        if (t.status === 'in_progress' && item.startMsgIdx === null) {
          item.startMsgIdx = msgIdx
          item.startAt = at
        }
        if ((t.status === 'completed' || t.status === 'cancelled') && item.endMsgIdx === null) {
          item.endMsgIdx = msgIdx
          item.endAt = at ?? item.startAt
        }
      }
    }
  })
  return trace
}

/** 步骤耗时文案:有起止锚点才显示,否则空串。 */
export function todoDurationText(t: TodoTraceItem): string {
  if (t.startAt == null || t.endAt == null || t.endAt < t.startAt) return ''
  const s = Math.max(0, Math.round((t.endAt - t.startAt) / 1000))
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m${s % 60}s`
}

/**
 * 从子代理(subagent)的消息数组中提取**最新一次** todowrite 快照。
 * 子代理的消息与主会话结构相同(role/content parts),复用 parseTodos;
 * 找不到任何 todo 快照时返回 null(例如子代理从未用过 todo 工具)。
 */
export function latestTodosFromMessages(
  msgs: TodoTraceMsg[],
): TodoItem[] | null {
  for (let i = (msgs?.length ?? 0) - 1; i >= 0; i--) {
    const m = msgs[i]
    if (!m || m.role !== 'assistant' || !Array.isArray(m.content)) continue
    for (let j = m.content.length - 1; j >= 0; j--) {
      const todos = parseTodos(
        m.content[j] as { name?: string; input?: unknown; result?: unknown } | null | undefined,
      )
      if (todos) return todos
    }
  }
  return null
}
