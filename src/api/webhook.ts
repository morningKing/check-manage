/**
 * Webhook API 调用模块
 */

import request from '@/utils/request'
import type {
  WebhookRule,
  CreateWebhookRuleRequest,
  UpdateWebhookRuleRequest,
  WebhookRuleTestResult,
  WebhookSettings,
  UpdateWebhookSettingsRequest,
  TestWebhookRequest,
  WebhookTestResult,
  WebhookLogListResponse,
  WebhookLog,
} from '@/types/webhook'

// ==================== Webhook Rules API ====================

/**
 * 获取 webhook 规则列表
 */
export function getWebhookRules(): Promise<WebhookRule[]> {
  return request.get('/webhook/rules')
}

/**
 * 创建 webhook 规则
 */
export function createWebhookRule(data: CreateWebhookRuleRequest): Promise<WebhookRule> {
  return request.post('/webhook/rules', data)
}

/**
 * 获取 webhook 规则详情
 */
export function getWebhookRule(ruleId: string): Promise<WebhookRule> {
  return request.get(`/webhook/rules/${ruleId}`)
}

/**
 * 更新 webhook 规则
 */
export function updateWebhookRule(ruleId: string, data: UpdateWebhookRuleRequest): Promise<WebhookRule> {
  return request.put(`/webhook/rules/${ruleId}`, data)
}

/**
 * 删除 webhook 规则
 */
export function deleteWebhookRule(ruleId: string): Promise<void> {
  return request.delete(`/webhook/rules/${ruleId}`)
}

/**
 * 测试 webhook 规则
 */
export function testWebhookRule(ruleId: string, customPayload?: Record<string, any>): Promise<WebhookRuleTestResult> {
  return request.post(`/webhook/rules/${ruleId}/test`, customPayload ? { customPayload } : {})
}

/**
 * 获取 webhook 规则日志
 */
export function getWebhookRuleLogs(ruleId: string, params?: {
  success?: string
  limit?: number
}): Promise<WebhookLogListResponse> {
  return request.get(`/webhook/rules/${ruleId}/logs`, { params })
}

// ==================== Legacy API (backward compatibility) ====================

/**
 * 获取 webhook 配置（旧版）
 */
export function getWebhookSettings(): Promise<WebhookSettings> {
  return request.get('/webhook/settings')
}

/**
 * 更新 webhook 配置（旧版）
 */
export function updateWebhookSettings(data: UpdateWebhookSettingsRequest): Promise<WebhookSettings> {
  return request.put('/webhook/settings', data)
}

/**
 * 测试 webhook 调用（旧版）
 */
export function testWebhook(data?: TestWebhookRequest): Promise<WebhookTestResult> {
  return request.post('/webhook/test', data || {})
}

/**
 * 获取 webhook 调用日志列表（旧版）
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