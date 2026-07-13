import { get, post, put, del } from '@/utils/request'
import type { EtlTask, EtlRunResult, EtlLog } from '@/types'
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

export function runEtlTask(id: string, options: { dryRun?: boolean } = {}) {
  return post<EtlRunResult>(`/etlTasks/${id}/run`, { dryRun: options.dryRun || false })
}

export function getEtlLogs(taskId: string) {
  return get<EtlLog[]>(`/etlTasks/${taskId}/logs`)
}

export function uploadEtlFile(file: File): Promise<DataFileMeta> {
  const form = new FormData()
  form.append('file', file)
  return post('/etlTasks/files/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
