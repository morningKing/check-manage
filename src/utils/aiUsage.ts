/**
 * 会话级 token 用量与上下文水位线计算（AI-Chat 优化 F1）。
 *
 * 数据源是每条 assistant 消息已持久化的 `meta`（`chat_persist` 在回合落库
 * 时写入 tokensInput/tokensOutput/cost），前端不另行存储——单一真源，消息
 * 删除/清空会话后自动一致，无需在 `ai_chat_sessions` 上另加累计列。
 *
 * 两个口径（Claude TUI 的 Context% 与 /cost 同款区分）：
 * - `contextTokens`：**当前上下文占用** ≈ 最近一个完成回合的 input+output。
 *   OpenCode 每步都带着全量上下文重发，`aggregate_metas` 在回合内取 input
 *   最大值，所以最后一条带 meta 的消息就是当前上下文的真实大小；/compact
 *   之后下一回合的 input 骤降，水位线随之回落——这正是水位线要的语义。
 * - `totalTokens`：**累计消耗** = 所有回合 input+output 之和（成本感知，
 *   单调递增，压缩不会让它变小）。
 */
import type { AiMessage } from '@/api/aiChat'

export interface SessionUsage {
  /** 当前上下文占用（tokens）；null = 尚无任何完成的回合 */
  contextTokens: number | null
  /** 累计消耗 tokens（所有回合 input+output 之和） */
  totalTokens: number
  /** 累计费用（USD） */
  cost: number
}

export const EMPTY_USAGE: SessionUsage = { contextTokens: null, totalTokens: 0, cost: 0 }

/** 从会话消息（含持久化 meta）计算用量。纯函数，幂等。 */
export function computeUsage(messages: AiMessage[] | undefined | null): SessionUsage {
  if (!messages || !messages.length) return { ...EMPTY_USAGE }
  let total = 0
  let cost = 0
  let context: number | null = null
  for (const m of messages) {
    if (!m || m.role !== 'assistant' || !m.meta) continue
    const tin = m.meta.tokensInput ?? 0
    const tout = m.meta.tokensOutput ?? 0
    if (!tin && !tout && !m.meta.cost) continue  // 无 token 信息的旧消息
    total += tin + tout
    cost += m.meta.cost ?? 0
    context = tin + tout
  }
  return { contextTokens: context, totalTokens: total, cost }
}

export type UsageLevel = 'ok' | 'warn' | 'danger'

/** 水位线分级：<70% 默认、70%~90% 警告、≥90% 危险（触发压缩建议）。 */
export function usageLevel(pct: number | null): UsageLevel {
  if (pct == null) return 'ok'
  if (pct >= 90) return 'danger'
  if (pct >= 70) return 'warn'
  return 'ok'
}

/** 上下文占用百分比；模型未声明窗口大小（limit 为空）时返回 null。 */
export function contextPercent(usage: SessionUsage, contextLimit: number | null | undefined): number | null {
  if (usage.contextTokens == null || !contextLimit || contextLimit <= 0) return null
  return (usage.contextTokens / contextLimit) * 100
}
