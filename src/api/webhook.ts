/**
 * Webhook API 调用模块
 */

import request from '@/utils/request'
import type {
  WebhookSettings,
  UpdateWebhookSettingsRequest,
  TestWebhookRequest,
  WebhookTestResult,
  WebhookLogListResponse,
  WebhookLog,
} from '@/types/webhook'

/**
 * 获取 webhook 配置
 */
export function getWebhookSettings(): Promise<WebhookSettings> {
  return request.get('/webhook/settings')
}

/**
 * 更新 webhook 配置
 */
export function updateWebhookSettings(data: UpdateWebhookSettingsRequest): Promise<WebhookSettings> {
  return request.put('/webhook/settings', data)
}

/**
 * 测试 webhook 调用
 */
export function testWebhook(data?: TestWebhookRequest): Promise<WebhookTestResult> {
  return request.post('/webhook/test', data || {})
}

/**
 * 获取 webhook 调用日志列表
 */
export function getWebhookLogs(params?: {
  eventType?: string
  success?: string
  limit?: number
  offset?: number
}): Promise<WebhookLogListResponse> {
  return request.get('/webhook/logs', { params })
}

/**
 * 获取 webhook 调用日志详情
 */
export function getWebhookLogDetail(logId: string): Promise<WebhookLog> {
  return request.get(`/webhook/logs/${logId}`)
}