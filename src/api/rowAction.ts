/**
 * 自定义行级操作按钮的 API
 */
import { post } from '@/utils/request'
import type { RunRowActionResult } from '@/types/rowAction'

/** 触发一个行操作。立即返回，实际执行在后端异步进行 */
export function runRowAction(
  collection: string,
  recordId: string,
  actionId: string,
  params: Record<string, unknown> = {},
): Promise<RunRowActionResult> {
  return post<RunRowActionResult>(
    `/${collection}/${recordId}/row-actions/${actionId}/run`,
    { params },
  )
}
