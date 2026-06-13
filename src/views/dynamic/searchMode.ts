/**
 * 数据页统一检索的三种模式。
 * 关键字：实时过滤；ai：自然语言生成筛选；mongo：高级 JSON 查询。
 */
export type SearchMode = 'keyword' | 'ai' | 'mongo'

export interface SearchQueryState {
  /** 当前是否存在 AI 生成的筛选条件 */
  hasAiFilter: boolean
  /** 当前是否存在已生效的 Mongo 查询 */
  hasMongoQuery: boolean
}

export interface SearchModeTransitionResult {
  clearAi: boolean
  clearMongo: boolean
}

/**
 * 判定从 from 模式切到 to 模式时应清除哪些已生效的查询。
 * 等价于旧 toggleAiSearch / toggleQueryMode 的互斥副作用。
 */
export function searchModeTransition(
  from: SearchMode,
  to: SearchMode,
  state: SearchQueryState
): SearchModeTransitionResult {
  if (from === to) return { clearAi: false, clearMongo: false }

  if (to === 'keyword') {
    return {
      clearAi: from === 'ai' && state.hasAiFilter,
      clearMongo: from === 'mongo' && state.hasMongoQuery,
    }
  }
  if (to === 'ai') {
    // 进入 AI：清掉游离的高级查询（但 AI 自身复用 activeMongoQuery，故有 AI 筛选时不清）
    return { clearAi: false, clearMongo: state.hasMongoQuery && !state.hasAiFilter }
  }
  // to === 'mongo'：进入高级查询，清掉 AI 筛选
  return { clearAi: state.hasAiFilter, clearMongo: false }
}
