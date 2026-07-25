import { get, post, put, del } from '@/utils/request'
import type { EtlTask, EtlRunResult, EtlRunStarted, EtlLog } from '@/types'
import type { DataFileMeta } from './dataFiles'

export function getEtlTasks() {
  return get<EtlTask[]>('/etlTasks')
}

export function createEtlTask(data: Partial<EtlTask>) {
  return post<EtlTask>('/etlTasks', data)
}

export function updateEtlTask(id: string, data: Partial<EtlTask>) {
  return put<EtlTask>(`/etlTasks/${id}`, data)
}

export function deleteEtlTask(id: string) {
  return del(`/etlTasks/${id}`)
}

/**
 * dryRun=true：同步返回完整结果（内部按 sampleSize 取样本，默认后端 50 条）。
 * dryRun=false（默认）：正式运行，立即返回 { logId, status: 'pending' }，
 * 真正的执行结果要用 getEtlLog 轮询。
 */
export function runEtlTask(
  id: string,
  options: { dryRun?: boolean; sampleSize?: number } = {}
): Promise<EtlRunResult | EtlRunStarted> {
  return post(`/etlTasks/${id}/run`, {
    dryRun: options.dryRun || false,
    sampleSize: options.sampleSize,
  })
}

export function getEtlLogs(taskId: string) {
  return get<EtlLog[]>(`/etlTasks/${taskId}/logs`)
}

export function getEtlLog(taskId: string, logId: string) {
  return get<EtlLog>(`/etlTasks/${taskId}/logs/${logId}`)
}

export function cancelEtlRun(taskId: string, logId: string) {
  return post(`/etlTasks/${taskId}/logs/${logId}/cancel`, {})
}

export function uploadEtlFile(file: File): Promise<DataFileMeta> {
  const form = new FormData()
  form.append('file', file)
  return post('/etlTasks/files/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
