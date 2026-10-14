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
  // 1.5s 后台轮询：失败由调用方转「连接中断」并继续重试，不能让全局拦截器
  // 弹 toast（后端重启/502 会变成循环 toast）或 401 时整页跳 /login（spec §5）。
  return get<LogTailResponse>('/ai/logs/tail', { file, offset, limit }, { silent: true })
}
