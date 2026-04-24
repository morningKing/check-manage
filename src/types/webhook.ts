/**
 * Webhook 类型定义
 */

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