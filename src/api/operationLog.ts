/**
 * 操作日志 API 接口
 */
import { get, del } from '@/utils/request'
import service from '@/utils/request'
import type { OperationLogQuery, OperationLogPage } from '@/types'

/**
 * 获取操作日志列表（分页 + 筛选）
 */
export function getOperationLogs(query: OperationLogQuery) {
  return get<OperationLogPage>('/operationLogs', query)
}

/**
 * 删除单条操作日志
 */
export function deleteOperationLog(id: string) {
  return del(`/operationLogs/${id}`)
}

/**
 * 导出操作日志为 Excel/CSV
 */
export async function exportOperationLogs(query: OperationLogQuery) {
  const response = await service.get('/operationLogs/export', {
    params: query,
    responseType: 'blob',
  })

  // 响应拦截器对 blob 返回完整 response
  const res = response as any
  const blob = new Blob([res.data])
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url

  // 从 content-disposition 获取文件名，或使用默认值
  const disposition = res.headers?.['content-disposition'] || ''
  const match = disposition.match(/filename=(.+)/)
  link.download = match ? match[1] : 'operation_logs.xlsx'

  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}
