/** aiChatBatches API 路径 id 兜底校验（requireId）测试。
 *
 *  上游传 null/undefined/'null'/空串时必须就地抛错（不发脏请求
 *  GET /ai/chat/batches/null，生产曾实测出现），合法 id 正常放行且被
 *  encodeURIComponent 编码。
 */
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/utils/request', () => ({
  get: vi.fn(async (url: string) => ({ url })),
  post: vi.fn(async (url: string) => ({ url })),
  del: vi.fn(async (url: string) => ({ url })),
  patch: vi.fn(async (url: string) => ({ url })),
}))

import { getBatch, stopBatch, reexecuteChild } from '@/api/aiChatBatches'

describe('aiChatBatches requireId guard', () => {
  it('refuses null/undefined/"null"/empty ids without issuing a request', () => {
    for (const bad of [null, undefined, 'null', ''] as unknown as string[]) {
      expect(() => getBatch(bad)).toThrow(/refusing to request/)
    }
    expect(() => stopBatch(null as unknown as string)).toThrow(/id is null/)
  })

  it('refuses null session id on child endpoints', () => {
    expect(() =>
      reexecuteChild('batch-1', null as unknown as string),
    ).toThrow(/sessionId is null/)
  })

  it('passes valid ids through encoded', async () => {
    const r = await getBatch('b/1') as unknown as { url: string }
    expect(r.url).toBe('/ai/chat/batches/b%2F1')
  })
})
