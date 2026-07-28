import { describe, it, expect, vi } from 'vitest'
import { importPageRecords, retryImportFailures } from '../importPageRecords'
import { makeStableImportId } from '../importId'

function makeStore(overrides: Record<string, any> = {}) {
  return {
    resolveRelationImportValues: vi.fn().mockResolvedValue(undefined),
    resolveReferenceImportValues: vi.fn().mockResolvedValue(undefined),
    resolveQuoteImportValues: vi.fn().mockResolvedValue(undefined),
    resolveCollectionSelectImportValues: vi.fn().mockResolvedValue(undefined),
    getResolveFieldCount: vi.fn().mockReturnValue(1),
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

    expect(store.resolveReferenceImportValues).toHaveBeenCalledWith('page-orders', records, expect.any(Map), expect.any(Function))
    expect(store.resolveRelationImportValues).toHaveBeenCalled()
    expect(store.resolveQuoteImportValues).toHaveBeenCalled()
    expect(store.resolveCollectionSelectImportValues).toHaveBeenCalled()
    expect(post).toHaveBeenCalledWith('/orders/batch-create', expect.objectContaining({
      records: expect.any(Array),
    }))
    expect(result).toEqual({ success: 2, failed: 0, created: 2, updated: 0, failures: [] })
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
    expect(result.failures).toHaveLength(1)
    expect(result.failures[0].originalRecord).toEqual({ name: 'a', _importId: expect.any(String) })
    expect(result.failures[0].reason).toMatch(/请求失败.*boom.*可重试/)
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
    expect(result).toEqual({ success: 2500, failed: 0, created: 2500, updated: 0, failures: [] })
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
    expect(result.failures).toHaveLength(1000)
    expect(result.failures[0].originalRecord.name).toBe('r0')
    expect(result.failures[0].reason).toMatch(/请求失败.*batch 0 boom.*可重试/)
  })

  it('captures per-record failures reported by the backend, keyed back to the original record', async () => {
    const store = makeStore()
    const post = vi.fn().mockResolvedValue({
      created: 1,
      updated: 0,
      failed: 1,
      errors: [{ index: 1, error: '主键重复', record: { id: 'orders-b' } }],
    })
    const records = [{ name: 'a' }, { name: 'b' }]

    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records,
    })

    expect(result.failed).toBe(1)
    expect(result.failures).toHaveLength(1)
    expect(result.failures[0].originalRecord).toEqual({ name: 'b', _importId: expect.any(String) })
    expect(result.failures[0].reason).toBe('主键重复')
    // payload 是已经构建好、可以直接重新 POST 的请求体
    expect(result.failures[0].payload).toEqual(post.mock.calls[0][1].records[1])
  })

  it('joins validationErrors into a single reason string when the backend reports a validation-script failure', async () => {
    const store = makeStore()
    const post = vi.fn().mockResolvedValue({
      created: 0,
      updated: 0,
      failed: 1,
      errors: [{
        index: 0,
        error: '校验失败',
        validationErrors: ['字段A必填', '字段B格式错误'],
        record: { id: 'orders-a' },
      }],
    })
    const records = [{ name: 'a' }]

    const result = await importPageRecords({
      store, post, pageId: 'page-orders', collection: 'orders', records,
    })

    expect(result.failures[0].reason).toBe('字段A必填; 字段B格式错误')
  })

  it('calls the four resolve functions sequentially, not concurrently', async () => {
    const callOrder: string[] = []
    const store = makeStore({
      resolveRelationImportValues: vi.fn(async () => { callOrder.push('start:relation'); await new Promise((r) => setTimeout(r, 5)); callOrder.push('end:relation') }),
      resolveReferenceImportValues: vi.fn(async () => { callOrder.push('start:reference'); callOrder.push('end:reference') }),
      resolveQuoteImportValues: vi.fn(async () => { callOrder.push('start:quote'); callOrder.push('end:quote') }),
      resolveCollectionSelectImportValues: vi.fn(async () => { callOrder.push('start:collectionSelect'); callOrder.push('end:collectionSelect') }),
    })
    const post = vi.fn().mockResolvedValue({ created: 1, updated: 0, failed: 0 })

    await importPageRecords({ store, post, pageId: 'page-orders', collection: 'orders', records: [{ name: 'a' }] })

    expect(callOrder).toEqual([
      'start:relation', 'end:relation',
      'start:reference', 'end:reference',
      'start:quote', 'end:quote',
      'start:collectionSelect', 'end:collectionSelect',
    ])
  })

  it('reports aggregated resolve progress across chunk callbacks from all four functions', async () => {
    const store = makeStore({
      getResolveFieldCount: vi.fn().mockReturnValue(2), // 2 个参与字段（比如 1 个 relation + 1 个 reference）
      resolveRelationImportValues: vi.fn(async (_p, records, _c, onChunkProcessed) => {
        onChunkProcessed?.(records.length)
      }),
      resolveReferenceImportValues: vi.fn(async (_p, records, _c, onChunkProcessed) => {
        onChunkProcessed?.(records.length)
      }),
    })
    const post = vi.fn().mockResolvedValue({ created: 3, updated: 0, failed: 0 })
    const onResolveProgress = vi.fn()
    const records = [{ name: 'a' }, { name: 'b' }, { name: 'c' }]

    await importPageRecords({ store, post, pageId: 'page-orders', collection: 'orders', records, onResolveProgress })

    // total = resolveFieldCount(2) * records.length(3) = 6；relation 报 3、reference 再报 3 → 累计 3 后 6
    expect(onResolveProgress).toHaveBeenNthCalledWith(1, 3, 6)
    expect(onResolveProgress).toHaveBeenNthCalledWith(2, 6, 6)
  })

  it('does not call onResolveProgress when the page has no resolve-eligible fields', async () => {
    const store = makeStore({ getResolveFieldCount: vi.fn().mockReturnValue(0) })
    const post = vi.fn().mockResolvedValue({ created: 1, updated: 0, failed: 0 })
    const onResolveProgress = vi.fn()

    await importPageRecords({ store, post, pageId: 'page-orders', collection: 'orders', records: [{ name: 'a' }], onResolveProgress })

    expect(onResolveProgress).not.toHaveBeenCalled()
    // resolveFieldCount === 0 时四个 resolve 函数也完全不必调用
    expect(store.resolveRelationImportValues).not.toHaveBeenCalled()
    expect(store.resolveReferenceImportValues).not.toHaveBeenCalled()
    expect(store.resolveQuoteImportValues).not.toHaveBeenCalled()
    expect(store.resolveCollectionSelectImportValues).not.toHaveBeenCalled()
  })
})

describe('retryImportFailures', () => {
  function makeFailure(overrides: Partial<import('../importPageRecords').ImportFailure> = {}) {
    return {
      originalRecord: { name: 'a' },
      payload: { id: 'orders-a', data: { name: 'a' }, relations: {} },
      reason: '主键重复',
      ...overrides,
    }
  }

  it('re-posts the given payloads directly, without any resolution step', async () => {
    const post = vi.fn().mockResolvedValue({ created: 1, updated: 0, failed: 0 })
    const failures = [makeFailure()]

    const result = await retryImportFailures(post, 'orders', failures)

    expect(post).toHaveBeenCalledWith('/orders/batch-create', {
      records: [failures[0].payload],
      options: { skipValidation: false, generateSequence: true, continueOnError: true },
    })
    expect(result).toEqual({ success: 1, failed: 0, created: 1, updated: 0, failures: [] })
  })

  it('splits more than BATCH_SIZE failures into multiple concurrent batches', async () => {
    const post = vi.fn().mockImplementation(async (_url: string, body: any) => ({
      created: body.records.length, updated: 0, failed: 0,
    }))
    const failures = Array.from({ length: 2500 }, (_, i) => makeFailure({
      originalRecord: { name: `r${i}` },
      payload: { id: `orders-${i}`, data: { name: `r${i}` }, relations: {} },
    }))
    const onProgress = vi.fn()

    const result = await retryImportFailures(post, 'orders', failures, onProgress)

    expect(post).toHaveBeenCalledTimes(3) // 1000 / 1000 / 500
    expect(result).toEqual({ success: 2500, failed: 0, created: 2500, updated: 0, failures: [] })
    const finalCall = onProgress.mock.calls[onProgress.mock.calls.length - 1]
    expect(finalCall).toEqual([2500, 2500])
  })

  it('returns only the still-failing subset, with the latest reason, after a partial retry', async () => {
    const post = vi.fn().mockResolvedValue({
      created: 1,
      updated: 0,
      failed: 1,
      errors: [{ index: 1, error: '仍然重复', record: {} }],
    })
    const failures = [makeFailure({ originalRecord: { name: 'a' } }), makeFailure({ originalRecord: { name: 'b' } })]

    const result = await retryImportFailures(post, 'orders', failures)

    expect(result.failed).toBe(1)
    expect(result.failures).toHaveLength(1)
    expect(result.failures[0].originalRecord).toEqual({ name: 'b' })
    expect(result.failures[0].reason).toBe('仍然重复')
  })

  it('marks all as still-failing with an updated reason when the whole retry request throws', async () => {
    const post = vi.fn().mockRejectedValue(new Error('还是超时'))
    const failures = [makeFailure()]

    const result = await retryImportFailures(post, 'orders', failures)

    expect(result.failed).toBe(1)
    expect(result.failures).toHaveLength(1)
    expect(result.failures[0].reason).toMatch(/请求失败.*还是超时.*可重试/)
  })
})
