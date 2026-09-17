/**
 * Summaries for OpenCode built-in tools (P2 spec §5.4): bash/read/write/edit/
 * glob/grep/task, etc.
 */
import type { ToolRenderer } from './types'
import { baseName, findError, truncate } from './generic'

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.trim() ? v.trim() : undefined
}

export const builtinRenderers: ToolRenderer[] = [
  {
    matches: ['bash'],
    displayName: '执行命令',
    inputSummary: (input) => {
      const cmd = str(input.command) ?? str(input.cmd)
      return cmd ? `执行命令“${truncate(cmd, 80)}”` : '执行命令'
    },
    resultSummary: (result) => {
      const err = findError(result)
      if (err) return `命令失败：${truncate(err)}`
      if (typeof result === 'string' && result.trim()) return truncate(result.split(/\r?\n/)[0] ?? '')
      return undefined
    },
  },
  {
    matches: ['read'],
    displayName: '读取文件',
    inputSummary: (input) => {
      const f = str(input.filePath) ?? str(input.file_path) ?? str(input.path)
      return f ? `读取文件“${baseName(f)}”` : '读取文件'
    },
  },
  {
    matches: ['write'],
    displayName: '写入文件',
    inputSummary: (input) => {
      const f = str(input.filePath) ?? str(input.file_path) ?? str(input.path)
      return f ? `写入文件“${baseName(f)}”` : '写入文件'
    },
  },
  {
    matches: ['edit'],
    displayName: '修改文件',
    inputSummary: (input) => {
      const f = str(input.filePath) ?? str(input.file_path) ?? str(input.path)
      return f ? `修改文件“${baseName(f)}”` : '修改文件'
    },
  },
  {
    matches: ['glob'],
    displayName: '查找匹配文件',
    inputSummary: (input) => {
      const p = str(input.pattern)
      return p ? `按模式“${truncate(p, 60)}”查找文件` : '查找匹配文件'
    },
    resultSummary: (result) => {
      if (Array.isArray(result)) return `匹配到 ${result.length} 个文件`
      return undefined
    },
  },
  {
    matches: ['grep'],
    displayName: '搜索文件内容',
    inputSummary: (input) => {
      const p = str(input.pattern)
      return p ? `搜索内容“${truncate(p, 60)}”` : '搜索文件内容'
    },
    resultSummary: (result) => {
      if (Array.isArray(result)) return `命中 ${result.length} 处`
      return undefined
    },
  },
  {
    matches: ['task'],
    displayName: '委托子代理',
    inputSummary: (input) => {
      const d = str(input.description)
      const a = str(input.subagent_type) ?? str(input.agent)
      let s = '委托子代理处理任务'
      if (a) s += `（${a}）`
      if (d) s += `：${truncate(d, 60)}`
      return s
    },
  },
  {
    matches: ['todowrite', 'todoread'],
    displayName: '更新执行计划',
    showRawInput: false,
  },
  {
    matches: ['webfetch'],
    displayName: '抓取网页',
    inputSummary: (input) => {
      const u = str(input.url)
      return u ? `抓取网页 ${truncate(u, 80)}` : '抓取网页'
    },
  },
]
