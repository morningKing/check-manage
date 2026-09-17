/**
 * Summaries for the platform's check-manage MCP tools (P2 spec §5.4).
 * Names are matched AFTER the `check-manage_` prefix is stripped.
 */
import type { ToolRenderer } from './types'
import { baseName, findError, findFilePath, parseResult, truncate } from './generic'

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.trim() ? v.trim() : undefined
}

function num(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

function countOf(result: unknown): number | undefined {
  const r = parseResult(result)
  if (Array.isArray(r)) return r.length
  if (r && typeof r === 'object') {
    const o = r as Record<string, unknown>
    if (typeof o.count === 'number') return o.count
    if (typeof o.total === 'number') return o.total
    for (const k of ['rows', 'items', 'records', 'data', 'results', 'memories']) {
      if (Array.isArray(o[k])) return (o[k] as unknown[]).length
    }
  }
  return undefined
}

export const checkManageRenderers: ToolRenderer[] = [
  {
    matches: ['query_collection'],
    displayName: '查询数据页',
    inputSummary: (input) => {
      const name = str(input.collection) ?? str(input.name) ?? str(input.collection_name)
      if (!name) return undefined
      const kw = str(input.keyword) ?? str(input.search) ?? str(input.query)
      const limit = num(input.limit)
      let s = `查询数据页“${name}”`
      if (kw) s += `，关键词为“${kw}”`
      if (limit != null) s += `，最多 ${limit} 条`
      return s
    },
    resultSummary: (result) => {
      const err = findError(result)
      if (err) return `查询失败：${truncate(err)}`
      const n = countOf(result)
      return n != null ? `找到 ${n} 条记录` : undefined
    },
  },
  {
    matches: ['list_collections'],
    displayName: '查看可用数据页',
    resultSummary: (result) => {
      const n = countOf(result)
      return n != null ? `共 ${n} 个数据页` : undefined
    },
  },
  {
    matches: ['read_upload'],
    displayName: '读取文件',
    inputSummary: (input) => {
      const f = str(input.path) ?? str(input.file) ?? str(input.name)
      return f ? `读取文件“${baseName(f)}”` : undefined
    },
  },
  {
    matches: ['save_artifact'],
    displayName: '生成文件',
    inputSummary: (input) => {
      const f = str(input.path) ?? str(input.filename) ?? str(input.file_name) ?? str(input.name)
      return f ? `生成文件“${baseName(f)}”` : undefined
    },
    resultSummary: (result) => {
      const err = findError(result)
      if (err) return `生成失败：${truncate(err)}`
      const p = result && typeof result === 'object' ? findFilePath(result as Record<string, unknown>) : undefined
      return p ? `已生成“${baseName(p)}”` : '已生成'
    },
  },
  {
    matches: ['run_python'],
    displayName: '执行 Python 任务',
    resultSummary: (result) => {
      const err = findError(result)
      return err ? `执行失败：${truncate(err)}` : undefined
    },
  },
  {
    matches: ['memory_search'],
    displayName: '搜索长期记忆',
    inputSummary: (input) => {
      const q = str(input.query) ?? str(input.keyword)
      return q ? `搜索长期记忆“${truncate(q, 40)}”` : '搜索长期记忆'
    },
    resultSummary: (result) => {
      const n = countOf(result)
      return n != null ? `命中 ${n} 条记忆` : undefined
    },
  },
  {
    matches: ['memory_add'],
    displayName: '添加长期记忆',
    inputSummary: (input) => {
      const t = str(input.text) ?? str(input.content)
      return t ? `添加长期记忆“${truncate(t, 60)}”` : '添加长期记忆'
    },
  },
  {
    matches: ['memory_delete'],
    displayName: '删除长期记忆',
    inputSummary: (input) => {
      const id = str(input.memory_id) ?? str(input.id)
      return id ? `删除长期记忆（${truncate(id, 24)}）` : '删除长期记忆'
    },
  },
  {
    matches: ['query_data', 'query_page'],
    displayName: '查询数据',
  },
]
