/**
 * 运行日志查看（2026-10-14 spec §4）：GET /ai/logs/tail 增量跟尾。
 */
import { get } from '@/utils/request'

export interface LogTailResponse {
  file: string
  offset: number
  size: number
  rotated: boolean
  truncated: boolean
  missing: boolean
  lines: string[]
}

export function fetchLogTail(
  file: 'chat' | 'serve',
  offset: number,
  limit = 300,
): Promise<LogTailResponse> {
  return get<LogTailResponse>('/ai/logs/tail', { file, offset, limit })
}
