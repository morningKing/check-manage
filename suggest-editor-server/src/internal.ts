/**
 * 服务端自己写 Y.Doc（suggestions / assistant 等旁路 map）时置位。
 * Hocuspocus onChange 钩子里用它过滤，避免「AI 写建议 → 触发新一轮分析」的回环。
 * Yjs 的 update 事件在 transact 内同步触发，因此简单的同步标志就足够。
 */
let depth = 0

export function withInternalWrite<T>(fn: () => T): T {
  depth++
  try {
    return fn()
  } finally {
    depth--
  }
}

export function isInternalWrite(): boolean {
  return depth > 0
}
