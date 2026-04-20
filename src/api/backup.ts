/**
 * 备份 API 接口
 */
import { get, post, del } from '@/utils/request'
import service from '@/utils/request'
import type { Backup, BackupSettings, DiffResult } from '@/types'

/**
 * 获取备份列表
 */
export function getBackups() {
  return get<Backup[]>('/backups')
}

/**
 * 创建备份
 * @param note 备注
 * @param tables 指定备份的表名列表，undefined = 全量备份
 */
export function createBackup(note?: string, tables?: string[]) {
  return post<Backup>('/backups', { note, tables })
}

/**
 * 删除备份
 */
export function deleteBackup(id: string) {
  return del(`/backups/${id}`)
}

/**
 * 下载备份 ZIP
 */
export async function downloadBackup(id: string, name: string) {
  const response = await service.get(`/backups/${id}/download`, {
    responseType: 'blob',
  })

  const res = response as any
  const blob = new Blob([res.data])
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url

  const disposition = res.headers?.['content-disposition'] || ''
  const match = disposition.match(/filename=(.+)/)
  link.download = match ? decodeURIComponent(match[1]) : `${name}.zip`

  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}

/**
 * 还原备份
 * @param id 备份ID
 * @param tables 指定还原的表名列表，undefined = 还原所有表
 */
export function restoreBackup(id: string, tables?: string[]) {
  return service.post(`/backups/${id}/restore`, { tables })
}

/**
 * 上传外部 ZIP 并还原
 */
export function uploadAndRestore(file: File) {
  const formData = new FormData()
  formData.append('file', file)
  return service.post('/backups/upload-restore', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

/**
 * 获取定时备份设置
 */
export function getBackupSettings() {
  return get<BackupSettings>('/backups/settings')
}

/**
 * 更新定时备份设置
 */
export function updateBackupSettings(settings: Partial<BackupSettings>) {
  return service.put('/backups/settings', settings)
}

/**
 * 获取可备份的表列表
 */
export function getBackupTables() {
  return get<{ name: string; label: string }[]>('/backups/tables')
}

/**
 * 对比两个数据源中指定集合的差异
 */
export function diffBackupCollection(
  collection: string,
  baseSource: string,
  targetSource: string
) {
  return post<DiffResult>('/backups/diff', { collection, baseSource, targetSource })
}

/**
 * 恢复出厂设置
 * @param confirmText 必须为 "RESET"
 */
export function factoryReset(confirmText: string) {
  return post<{
    message: string
    deletedTables: string[]
    deletedRecords: Record<string, number>
    backupId: string
    timestamp: string
  }>('/backups/factory-reset', { confirmText })
}
