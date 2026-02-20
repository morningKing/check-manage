import { get, post, put, del } from '@/utils/request'
import type { EtlTask, EtlRunResult, EtlLog } from '@/types'

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

export function runEtlTask(id: string, options: { dryRun?: boolean } = {}) {
  return post<EtlRunResult>(`/etlTasks/${id}/run`, { dryRun: options.dryRun || false })
}

export function getEtlLogs(taskId: string) {
  return get<EtlLog[]>(`/etlTasks/${taskId}/logs`)
}
