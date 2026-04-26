/**
 * Webhook 类型定义
 */

// ==================== Webhook Rule Types ====================

export interface WebhookRule {
  id: string
  name: string
  description?: string
  enabled: boolean
  sourceCollections: string[]  // 数组，多个数据页；空数组表示全局（如 merge）
  triggerEvent: 'create' | 'update' | 'delete' | 'merge'
  triggerTiming: 'before' | 'after'  // 新增：触发时机
  rollbackOnFailure: boolean          // 新增：失败时是否回滚（仅 merge + after）
  triggerCondition?: Record<string, any>
  webhookUrl: string
  secret?: string
  timeout: number
  retries: number
  executionOrder: number
  createdAt?: string
  updatedAt?: string
  createdBy?: string
  updatedBy?: string
}

export interface CreateWebhookRuleRequest {
  id?: string
  name: string
  description?: string
  enabled?: boolean
  sourceCollections?: string[]
  triggerEvent: 'create' | 'update' | 'delete' | 'merge'
  triggerTiming?: 'before' | 'after'  // 新增
  rollbackOnFailure?: boolean          // 新增
  triggerCondition?: Record<string, any>
  webhookUrl: string
  secret?: string
  timeout?: number
  retries?: number
  executionOrder?: number
}

export interface UpdateWebhookRuleRequest {
  name?: string
  description?: string
  enabled?: boolean
  sourceCollections?: string[]
  triggerEvent?: 'create' | 'update' | 'delete' | 'merge'
  triggerTiming?: 'before' | 'after'  // 新增
  rollbackOnFailure?: boolean          // 新增
  triggerCondition?: Record<string, any>
  webhookUrl?: string
  secret?: string
  timeout?: number
  retries?: number
  executionOrder?: number
}

export interface WebhookRuleListResponse {
  rules: WebhookRule[]
}

export interface WebhookRuleTestResult {
  success: boolean
  logId?: string
  responseStatus?: number
  errorMessage?: string
  retryCount?: number
}

// ==================== Legacy Types (backward compatibility) ====================

export interface WebhookSettings {
  enabled: boolean
  name: string
  webhookUrl: string
  secret: string
  events: string[]
  timeout: number
  retries: number
  updatedAt?: string
  updatedBy?: string
}

export interface WebhookLog {
  id: string
  ruleId?: string
  ruleName?: string
  webhookUrl: string
  eventType: string
  requestPayload: Record<string, any>
  responseStatus?: number
  responseBody?: string
  errorMessage?: string
  durationMs: number
  retryCount: number
  success: boolean
  createdAt: string
}

export interface UpdateWebhookSettingsRequest {
  enabled?: boolean
  name?: string
  webhookUrl?: string
  secret?: string
  events?: string[]
  timeout?: number
  retries?: number
}

export interface TestWebhookRequest {
  webhookUrl?: string
  event?: string
}

export interface WebhookTestResult {
  success: boolean
  logId?: string
  responseStatus?: number
  errorMessage?: string
  retryCount?: number
  message?: string
}

export interface WebhookLogListResponse {
  logs: WebhookLog[]
  total: number
}