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
