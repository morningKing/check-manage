/**
 * 备份相关类型定义
 */

export interface Backup {
  id: string
  name: string
  type: 'manual' | 'scheduled'
  status: 'completed' | 'failed'
  fileSize: number
  tablesCount: number
  recordsCount: number
  createdBy: string
  createdAt: string
  note: string | null
}

export interface BackupSettings {
  enabled: boolean
  interval: 'daily' | 'weekly' | 'monthly'
  retentionCount: number
  lastBackupAt: string | null
  updatedAt: string
}

export const BACKUP_TYPE_LABELS: Record<string, string> = {
  manual: '手动备份',
  scheduled: '定时备份',
}

export const BACKUP_TYPE_TAG_TYPES: Record<string, string> = {
  manual: '',
  scheduled: 'success',
}

export const BACKUP_STATUS_LABELS: Record<string, string> = {
  completed: '已完成',
  failed: '失败',
}

export const BACKUP_STATUS_TAG_TYPES: Record<string, string> = {
  completed: 'success',
  failed: 'danger',
}

export const BACKUP_INTERVAL_OPTIONS = [
  { label: '每天', value: 'daily' },
  { label: '每周', value: 'weekly' },
  { label: '每月', value: 'monthly' },
]
