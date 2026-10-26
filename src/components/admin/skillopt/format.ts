/** 毫秒时长格式化：null → '-'；<1s 显示 ms；<60s 显示一位小数秒；
 *  ≥60s 显示 m s——秒先按四舍五入收敛再拆分钟，杜绝 "1m60s" 进位缺陷。 */
export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const totalSec = Math.round(ms / 1000)
  return `${Math.floor(totalSec / 60)}m${totalSec % 60}s`
}

/** 比率 → 百分比字符串：四舍五入取整，null → '-'。 */
export function pct(r: number | null | undefined): string {
  return r == null ? '-' : `${Math.round(r * 100)}%`
}

/** 慢因四段元数据（与任务抽屉覆盖占比条同源同色：模型蓝/等待橙/工具绿/间隙灰）。
 *  key 顺序即堆叠条铺排顺序。 */
export const SEGMENT_META = {
  model: { label: '模型推理', color: '#409eff' },
  wait: { label: '子代理等待', color: '#e6a23c' },
  tool: { label: '工具', color: '#67c23a' },
  idle: { label: '引擎间隙', color: '#909399' },
} as const
