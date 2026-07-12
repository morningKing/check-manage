/**
 * statusBadge 字段的页面级轮询：判定当前页数据里是否还有非终态行，
 * 有则按配置的间隔调度一次刷新回调；全部终态或没有 statusBadge 字段则不调度。
 *
 * 用 setTimeout（不用 setInterval）：每次都等上一次 onRefresh 完成后才重新判定
 * 要不要调度下一次，避免刷新耗时超过间隔时产生重叠请求。
 */
import { ref, type Ref } from 'vue'
import type { FieldConfig } from '@/types'

const DEFAULT_POLL_INTERVAL_SEC = 5

export function useStatusBadgePolling(options: {
  fields: Ref<FieldConfig[]>
  onRefresh: () => Promise<void>
}) {
  const timer = ref<ReturnType<typeof setTimeout> | null>(null)

  function statusBadgeFields(): FieldConfig[] {
    return options.fields.value.filter(f => f.controlType === 'statusBadge' && f.statusBadgeConfig)
  }

  function isNonTerminal(row: any, field: FieldConfig): boolean {
    const cfg = field.statusBadgeConfig
    if (!cfg) return false
    const value = row[field.fieldName]
    const opt = cfg.options.find(o => o.value === value)
    return !!opt && !opt.terminal
  }

  function hasNonTerminalRows(rows: any[]): boolean {
    const fields = statusBadgeFields()
    if (fields.length === 0) return false
    return rows.some(row => fields.some(f => isNonTerminal(row, f)))
  }

  function minPollIntervalMs(): number {
    const fields = statusBadgeFields()
    const seconds = fields.map(f => f.statusBadgeConfig?.pollIntervalSec ?? DEFAULT_POLL_INTERVAL_SEC)
    return Math.min(...seconds) * 1000
  }

  function stop(): void {
    if (timer.value !== null) {
      clearTimeout(timer.value)
      timer.value = null
    }
  }

  function evaluateAndSchedule(rows: any[]): void {
    stop()
    if (!hasNonTerminalRows(rows)) return
    timer.value = setTimeout(() => {
      options.onRefresh()
    }, minPollIntervalMs())
  }

  return { evaluateAndSchedule, stop }
}
