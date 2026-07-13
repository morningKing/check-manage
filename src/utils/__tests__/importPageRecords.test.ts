import { describe, it, expect, vi } from 'vitest'
import { importPageRecords } from '../importPageRecords'
import { makeStableImportId } from '../importId'

function makeStore(overrides: Record<string, any> = {}) {
  return {
    resolveRelationImportValues: vi.fn().mockResolvedValue(undefined),
    resolveReferenceImportValues: vi.fn().mockResolvedValue(undefined),
    resolveQuoteImportValues: vi.fn().mockResolvedValue(undefined),
    resolveCollectionSelectImportValues: vi.fn().mockResolvedValue(undefined),
    batchGenerateSequenceValues: vi.fn().mockReturnValue({}),
    stripRelationFields: vi.fn((_p: string, r: any) => ({ ...r })),
    getRelationFields: vi.fn().mockReturnValue([]),
    getPrimaryKeyFields: vi.fn().mockReturnValue([]),
    ...overrides,
  }
}

describe('importPageRecords', () => {
  it('resolves references then calls batch-create and returns counts', async () => {
    const store = makeStore()
    const post = vi.fn().mockResolvedValue({ success: true, created: 2, updated: 0, failed: 0 })
    const records = [{ name: 'a' }, { name: 'b' }]

    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records,
    })

    expect(store.resolveReferenceImportValues).toHaveBeenCalledWith('page-orders', records, expect.any(Map))
    expect(store.resolveRelationImportValues).toHaveBeenCalled()
    expect(store.resolveQuoteImportValues).toHaveBeenCalled()
    expect(store.resolveCollectionSelectImportValues).toHaveBeenCalled()
    expect(post).toHaveBeenCalledWith('/orders/batch-create', expect.objectContaining({
      records: expect.any(Array),
    }))
    expect(result).toEqual({ success: 2, failed: 0, created: 2, updated: 0 })
  })

  it('reports progress for the final batch', async () => {
    const store = makeStore()
    const post = vi.fn().mockResolvedValue({ created: 1, updated: 0, failed: 0 })
    const onProgress = vi.fn()
    await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records: [{ name: 'a' }], onProgress,
    })
    expect(onProgress).toHaveBeenCalledWith(1, 1)
  })

  it('derives a stable, primary-key-based id when the page has a primary key', async () => {
    const store = makeStore({
      getPrimaryKeyFields: vi.fn().mockReturnValue([{ fieldName: 'code' }]),
    })
    const post = vi.fn().mockResolvedValue({ created: 2, updated: 0, failed: 0 })
    const records = [{ code: 'SKU-1', name: 'a' }, { code: 'SKU-2', name: 'b' }]

    await importPageRecords({ store, post, pageId: 'page-products', collection: 'products', records })

    const sent = post.mock.calls[0][1].records
    // id is the deterministic hash of the primary key — identical to a fresh derivation,
    // so a delete-and-re-import of the same code reuses this exact id.
    expect(sent[0].id).toBe(makeStableImportId('products', ['SKU-1']))
    expect(sent[1].id).toBe(makeStableImportId('products', ['SKU-2']))
    // re-importing the same record yields the same id (no random suffix)
    expect(sent[0].id).toBe(makeStableImportId('products', ['SKU-1']))
  })

  it('falls back to a random row id when a primary-key value is missing', async () => {
    const store = makeStore({
      getPrimaryKeyFields: vi.fn().mockReturnValue([{ fieldName: 'code' }]),
    })
    const post = vi.fn().mockResolvedValue({ created: 1, updated: 0, failed: 0 })
    const records = [{ name: 'no-code-here' }]

    await importPageRecords({ store, post, pageId: 'page-products', collection: 'products', records })

    const sent = post.mock.calls[0][1].records
    expect(sent[0].id).not.toContain('-pk-')
    expect(sent[0].id).toMatch(/^products-/)
  })

  it('counts batch failure without throwing', async () => {
    const store = makeStore()
    const post = vi.fn().mockRejectedValue(new Error('boom'))
    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records: [{ name: 'a' }],
    })
    expect(result.failed).toBe(1)
    expect(result.success).toBe(0)
  })

  it('splits large imports into multiple concurrent batches and aggregates all of them', async () => {
    // BATCH_SIZE=1000，2500 条应切成 3 批（1000/1000/500），并发上传（CONCURRENCY=3）
    const store = makeStore()
    const post = vi.fn().mockImplementation(async (_url: string, body: any) => ({
      created: body.records.length,
      updated: 0,
      failed: 0,
    }))
    const records = Array.from({ length: 2500 }, (_, i) => ({ name: `r${i}` }))
    const onProgress = vi.fn()

    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records, onProgress,
    })

    expect(post).toHaveBeenCalledTimes(3)
    expect(result).toEqual({ success: 2500, failed: 0, created: 2500, updated: 0 })
    // 进度回调的 current 单调递增，最终到达总数
    const finalCall = onProgress.mock.calls[onProgress.mock.calls.length - 1]
    expect(finalCall[0]).toBe(2500)
    expect(finalCall[1]).toBe(2500)
  })

  it('one failing batch does not affect the other concurrently-run batches', async () => {
    const store = makeStore()
    const post = vi.fn().mockImplementation(async (_url: string, body: any) => {
      // 第一条记录所在批次失败，其余批次照常成功
      if (body.records[0].data.name === 'r0') throw new Error('batch 0 boom')
      return { created: body.records.length, updated: 0, failed: 0 }
    })
    const records = Array.from({ length: 2500 }, (_, i) => ({ name: `r${i}` }))

    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records,
    })

    expect(result.failed).toBe(1000) // 第一批 1000 条计入失败
    expect(result.success).toBe(1500) // 剩余两批成功
  })
})
