/**
 * API Key 相关类型定义
 */

export interface ApiKey {
  id: string
  name: string
  key?: string
  createdAt: string
  lastUsedAt: string | null
  isActive: boolean
}
