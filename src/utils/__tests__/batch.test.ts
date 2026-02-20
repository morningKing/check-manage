import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('uuid', () => ({
  v4: vi.fn(() => 'aaaabbbb-cccc-dddd-eeee-ffffffffffff'),
}))

import { getBatchHeaders, withBatch } from '../batch'

describe('Batch Utils', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getBatchHeaders 无 batch 时返回空对象', () => {
    expect(getBatchHeaders()).toEqual({})
  })

  it('withBatch 执行期间 getBatchHeaders 返回正确头', async () => {
    let capturedHeaders: Record<string, string> = {}

    await withBatch('批量导入', async () => {
      capturedHeaders = getBatchHeaders()
      return 'result'
    })

    expect(capturedHeaders['X-Batch-Id']).toMatch(/^batch-/)
    expect(capturedHeaders['X-Batch-Desc']).toBe(encodeURIComponent('批量导入'))
  })

  it('withBatch 返回 fn 的返回值', async () => {
    const result = await withBatch('测试', async () => {
      return 42
    })
    expect(result).toBe(42)
  })

  it('withBatch 完成后 headers 被清理', async () => {
    await withBatch('测试', async () => 'ok')
    expect(getBatchHeaders()).toEqual({})
  })

  it('withBatch 异常后 headers 也被清理', async () => {
    try {
      await withBatch('出错批次', async () => {
        throw new Error('boom')
      })
    } catch {
      // expected
    }
    expect(getBatchHeaders()).toEqual({})
  })

  it('withBatch 异常后重新抛出', async () => {
    await expect(
      withBatch('出错', async () => {
        throw new Error('test error')
      })
    ).rejects.toThrow('test error')
  })
})
