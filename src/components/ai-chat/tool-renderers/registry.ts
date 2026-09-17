/**
 * Tool-call summary registry (P2 spec §5.3): resolves a renderer for a tool
 * name and builds the user-facing summary strings. Deterministic rules only.
 */
import type { ToolRenderContext, ToolRenderer } from './types'
import { TOOL_STATUS_TEXT } from './types'
import { checkManageRenderers } from './checkManage'
import { builtinRenderers } from './builtin'
import { findError, parseResult, truncate } from './generic'

export * from './types'
export { SENSITIVE_KEYS, isSensitiveKey, maskSensitiveValue, redact,
         stringifyRedacted, firstLine, baseName, parseResult, findError,
         findFilePath, truncate } from './generic'
export { checkManageRenderers } from './checkManage'
export { builtinRenderers } from './builtin'

const RENDERERS: ToolRenderer[] = [...checkManageRenderers, ...builtinRenderers]

/** `check-manage_xxx` → `xxx`; also trims a few other common MCP prefixes. */
export function normalizeToolName(name: string): string {
  return (name || '').replace(/^check-manage_/, '').trim()
}

export function resolveRenderer(name: string): { renderer?: ToolRenderer; normalized: string } {
  const normalized = normalizeToolName(name)
  const lower = normalized.toLowerCase()
  return { renderer: RENDERERS.find(r => r.matches.includes(lower)), normalized }
}

export interface ToolSummary {
  /** Display name, e.g. 查询数据页 / bash */
  displayName: string
  /** Collapsed-head action summary, e.g. 查询数据页“订单”，关键词为“供应商” */
  actionText: string
  /** Result summary, e.g. 找到 24 条记录 */
  resultText: string
  /** Canonical status text (等待执行/正在执行/已完成/…), never empty. */
  statusText: string
  /** Whether the raw input JSON section may be shown. */
  showRawInput: boolean
  /** Extra sensitive keys from the resolved renderer. */
  sensitiveKeys: string[]
}

function asInputObject(input: unknown): Record<string, unknown> {
  return input && typeof input === 'object' && !Array.isArray(input)
    ? input as Record<string, unknown>
    : {}
}

/** Build the full summary for a tool call. Never throws — every stage degrades. */
export function buildToolSummary(ctx: ToolRenderContext): ToolSummary {
  const { renderer, normalized } = resolveRenderer(ctx.name)
  const displayName = renderer?.displayName ?? (normalized || 'tool')

  let actionText = ''
  if (renderer?.inputSummary) {
    try { actionText = renderer.inputSummary(asInputObject(ctx.input), ctx) ?? '' } catch { /* degrade */ }
  }
  if (!actionText) {
    // Unknown-tool degradation (§5.5): 调用工具“tool-name” [· target]
    const target = ctx.title?.trim()
    actionText = target && target !== ctx.name
      ? `调用工具“${normalized || ctx.name}” · ${truncate(target, 60)}`
      : `调用工具“${normalized || ctx.name}”`
  }

  let resultText = ''
  const error = findError(ctx.result)
  if (error) {
    resultText = `执行失败：${truncate(error)}`
  } else if (renderer?.resultSummary) {
    try { resultText = renderer.resultSummary(parseResult(ctx.result), ctx) ?? '' } catch { /* degrade */ }
  } else {
    resultText = genericResultSummary(parseResult(ctx.result))
  }

  return {
    displayName,
    actionText,
    resultText,
    statusText: TOOL_STATUS_TEXT[ctx.status ?? ''] ?? '状态未知',
    showRawInput: renderer?.showRawInput !== false,
    sensitiveKeys: renderer?.sensitiveKeys ?? [],
  }
}

/** §5.6 generic result summary for tools without a dedicated renderer. */
export function genericResultSummary(result: unknown): string {
  if (result == null || result === '') return '执行完成，无返回结果'
  if (typeof result === 'string') {
    const t = result.trim()
    return t ? truncate(t.split(/\r?\n/)[0] ?? '') : '执行完成，无返回结果'
  }
  if (Array.isArray(result)) {
    return result.length ? `找到 ${result.length} 项` : '找到 0 项'
  }
  if (typeof result === 'object') {
    const o = result as Record<string, unknown>
    if (typeof o.count === 'number') return `共 ${o.count} 项`
    for (const k of ['rows', 'items', 'records', 'results']) {
      if (Array.isArray(o[k])) return `找到 ${(o[k] as unknown[]).length} 项`
    }
    return '执行完成'
  }
  return '执行完成'
}
