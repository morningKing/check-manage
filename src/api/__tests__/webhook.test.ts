import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/request', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

import request from '@/utils/request'
import {
  getWebhookRules,
  createWebhookRule,
  getWebhookRule,
  updateWebhookRule,
  deleteWebhookRule,
  testWebhookRule,
  getWebhookRuleLogs,
  getWebhookSettings,
  updateWebhookSettings,
  testWebhook,
  getWebhookLogs,
} from '../webhook'

const mockGet = vi.mocked(request.get)
const mockPost = vi.mocked(request.post)
const mockPut = vi.mocked(request.put)
const mockDelete = vi.mocked(request.delete)

describe('Webhook Rules API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('getWebhookRules', () => {
    it('调用 GET /webhook/rules', async () => {
      const rules = [{ id: 'whrule-1', name: '规则1', triggerEvent: 'create' }]
      mockGet.mockResolvedValueOnce(rules as any)

      const res = await getWebhookRules()
      expect(mockGet).toHaveBeenCalledWith('/webhook/rules')
      expect(res).toEqual(rules)
    })
  })

  describe('createWebhookRule', () => {
    it('调用 POST /webhook/rules', async () => {
      const data = {
        name: '新规则',
        triggerEvent: 'create' as const,
        webhookUrl: 'https://example.com',
        sourceCollections: ['orders', 'products'],
      }
      mockPost.mockResolvedValueOnce({ id: 'whrule-2', ...data } as any)

      const res = await createWebhookRule(data)
      expect(mockPost).toHaveBeenCalledWith('/webhook/rules', data)
      expect(res.id).toBe('whrule-2')
    })
  })

  describe('getWebhookRule', () => {
    it('调用 GET /webhook/rules/{id}', async () => {
      const rule = { id: 'whrule-1', name: '规则1' }
      mockGet.mockResolvedValueOnce(rule as any)

      const res = await getWebhookRule('whrule-1')
      expect(mockGet).toHaveBeenCalledWith('/webhook/rules/whrule-1')
      expect(res).toEqual(rule)
    })
  })

  describe('updateWebhookRule', () => {
    it('调用 PUT /webhook/rules/{id}', async () => {
      const data = { name: '更新规则', enabled: false }
      mockPut.mockResolvedValueOnce({ id: 'whrule-1', ...data } as any)

      const res = await updateWebhookRule('whrule-1', data)
      expect(mockPut).toHaveBeenCalledWith('/webhook/rules/whrule-1', data)
      expect(res.name).toBe('更新规则')
    })
  })

  describe('deleteWebhookRule', () => {
    it('调用 DELETE /webhook/rules/{id}', async () => {
      mockDelete.mockResolvedValueOnce(undefined as any)

      await deleteWebhookRule('whrule-1')
      expect(mockDelete).toHaveBeenCalledWith('/webhook/rules/whrule-1')
    })
  })

  describe('testWebhookRule', () => {
    it('调用 POST /webhook/rules/{id}/test', async () => {
      const result = { success: true, logId: 'log-1' }
      mockPost.mockResolvedValueOnce(result as any)

      const res = await testWebhookRule('whrule-1')
      expect(mockPost).toHaveBeenCalledWith('/webhook/rules/whrule-1/test', {})
      expect(res.success).toBe(true)
    })

    it('支持自定义 payload', async () => {
      const customPayload = { test: true }
      mockPost.mockResolvedValueOnce({ success: true } as any)

      await testWebhookRule('whrule-1', customPayload)
      expect(mockPost).toHaveBeenCalledWith('/webhook/rules/whrule-1/test', { customPayload })
    })
  })

  describe('getWebhookRuleLogs', () => {
    it('调用 GET /webhook/rules/{id}/logs', async () => {
      const logs = { logs: [{ id: 'log-1', success: true }], total: 1 }
      mockGet.mockResolvedValueOnce(logs as any)

      const res = await getWebhookRuleLogs('whrule-1')
      expect(mockGet).toHaveBeenCalledWith('/webhook/rules/whrule-1/logs', { params: undefined })
      expect(res.logs).toHaveLength(1)
    })

    it('支持查询参数', async () => {
      const logs = { logs: [], total: 0 }
      mockGet.mockResolvedValueOnce(logs as any)

      await getWebhookRuleLogs('whrule-1', { success: 'true', limit: 10 })
      expect(mockGet).toHaveBeenCalledWith('/webhook/rules/whrule-1/logs', { params: { success: 'true', limit: 10 } })
    })
  })
})

describe('Legacy Webhook API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('getWebhookSettings', () => {
    it('调用 GET /webhook/settings', async () => {
      const settings = { enabled: true, name: '合并通知', webhookUrl: 'https://example.com' }
      mockGet.mockResolvedValueOnce(settings as any)

      const res = await getWebhookSettings()
      expect(mockGet).toHaveBeenCalledWith('/webhook/settings')
      expect(res.enabled).toBe(true)
    })
  })

  describe('updateWebhookSettings', () => {
    it('调用 PUT /webhook/settings', async () => {
      const data = { enabled: true, webhookUrl: 'https://example.com' }
      mockPut.mockResolvedValueOnce(data as any)

      await updateWebhookSettings(data)
      expect(mockPut).toHaveBeenCalledWith('/webhook/settings', data)
    })
  })

  describe('testWebhook', () => {
    it('调用 POST /webhook/test', async () => {
      const result = { success: true, logId: 'log-1' }
      mockPost.mockResolvedValueOnce(result as any)

      const res = await testWebhook()
      expect(mockPost).toHaveBeenCalledWith('/webhook/test', {})
      expect(res.success).toBe(true)
    })

    it('支持自定义 URL', async () => {
      const result = { success: false, errorMessage: 'Timeout' }
      mockPost.mockResolvedValueOnce(result as any)

      await testWebhook({ webhookUrl: 'https://custom.com' })
      expect(mockPost).toHaveBeenCalledWith('/webhook/test', { webhookUrl: 'https://custom.com' })
    })
  })

  describe('getWebhookLogs', () => {
    it('调用 GET /webhook/logs', async () => {
      const logs = { logs: [{ id: 'log-1' }], total: 1 }
      mockGet.mockResolvedValueOnce(logs as any)

      const res = await getWebhookLogs()
      expect(mockGet).toHaveBeenCalledWith('/webhook/logs', { params: undefined })
      expect(res.total).toBe(1)
    })

    it('支持查询参数', async () => {
      const logs = { logs: [], total: 0 }
      mockGet.mockResolvedValueOnce(logs as any)

      await getWebhookLogs({ success: 'false', limit: 20 })
      expect(mockGet).toHaveBeenCalledWith('/webhook/logs', { params: { success: 'false', limit: 20 } })
    })
  })
})