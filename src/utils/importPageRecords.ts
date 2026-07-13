import { makeImportRowId, makeStableImportId } from '@/utils/importId'

export interface ImportPageResult {
  success: number
  failed: number
  created: number
  updated: number
}

export interface ImportPageParams {
  store: any // pageConfig store（解析/序列/关联方法）
  post: <T>(url: string, body: any) => Promise<T> // 注入 @/utils/request 的 post
  pageId: string
  collection: string
  records: Record<string, any>[]
  onProgress?: (current: number, total: number) => void
}

const BATCH_SIZE = 1000
// 并发上传批次数。批次内部分主键校验是逐条查库的（见后端 check_primary_key_unique），
// 单批太大会让单个请求变慢甚至超时；并发送多个中等大小的批次比"更大的单批"更稳妥，
// 也比纯串行快数倍。3 是留有余量的保守值（后端 waitress 线程池默认 8，DB 连接池 20）。
const CONCURRENCY = 3

/**
 * 单页批量导入核心：盖保序 id → 解析引用/关联 → 生成序列 → 分批 batch-create。
 * 不含任何 Vue 响应式依赖，供 DynamicPage 与批量导入编排共用。
 */
export async function importPageRecords(params: ImportPageParams): Promise<ImportPageResult> {
  const { store, post, pageId, collection, records, onProgress } = params
  const collectionCache = new Map<string, any[]>()

  // 主键派生的确定性 id：有主键字段且该行主键值齐全时，用主键哈希出稳定 id，
  // 使「删除后重新导入」复用同一 id（后端 batch-create 是 upsert），从而让引用方
  // 存的旧 id 自动保持有效；否则回退到带随机后缀、保序的行 id。
  const pkFieldNames: string[] = (store.getPrimaryKeyFields?.(pageId) || []).map(
    (f: { fieldName: string }) => f.fieldName
  )
  const hasPk = (r: Record<string, any>): boolean =>
    pkFieldNames.length > 0 &&
    pkFieldNames.every((fn) => {
      const v = r[fn]
      return v !== undefined && v !== null && String(v).trim() !== ''
    })

  records.forEach((r, i) => {
    if (r._importId) return
    r._importId = hasPk(r)
      ? makeStableImportId(collection, pkFieldNames.map((fn) => r[fn]))
      : makeImportRowId(collection, i, records.length)
  })

  await Promise.all([
    store.resolveRelationImportValues(pageId, records, collectionCache),
    store.resolveReferenceImportValues(pageId, records, collectionCache),
    store.resolveQuoteImportValues(pageId, records, collectionCache),
    store.resolveCollectionSelectImportValues(pageId, records, collectionCache),
  ])

  const sequenceValues = store.batchGenerateSequenceValues(pageId, records.length)
  const sequenceFields = Object.keys(sequenceValues)

  let success = 0, failed = 0, created = 0, updated = 0
  let completed = 0
  const batchCount = Math.ceil(records.length / BATCH_SIZE)

  async function runBatch(batchIdx: number): Promise<void> {
    const start = batchIdx * BATCH_SIZE
    const end = Math.min(start + BATCH_SIZE, records.length)
    const batchRecords = records.slice(start, end)

    try {
      const batchData = batchRecords.map((record, idx) => {
        const importId = record._importId as string | undefined
        const regularData = store.stripRelationFields(pageId, record)
        delete regularData._importId
        for (const fieldName of sequenceFields) {
          if (!regularData[fieldName]) regularData[fieldName] = sequenceValues[fieldName][start + idx]
        }
        const relations: Record<string, string[]> = {}
        for (const field of store.getRelationFields(pageId)) {
          const ids = record[field.fieldName]
          if (Array.isArray(ids) && ids.length > 0) relations[field.fieldName] = ids
        }
        return {
          // id 正常来自上面的 _importId 盖章；此为防御性兜底
          id: importId || `${collection}-${Math.random().toString(36).slice(2, 10)}`,
          data: regularData,
          relations,
        }
      })

      const result = await post<{ created: number; updated?: number; failed: number; errors?: Array<{ index: number; error: string; record: any }> }>(
        `/${collection}/batch-create`,
        { records: batchData, options: { skipValidation: false, generateSequence: true, continueOnError: true } },
      )
      success += result.created + (result.updated || 0)
      created += result.created
      updated += result.updated || 0
      failed += result.failed
      if (result.errors && result.errors.length > 0) {
        console.warn(`批次 ${batchIdx + 1} 失败记录:`, result.errors)
      }
    } catch (error) {
      console.error(`批次 ${batchIdx + 1} 导入失败:`, error)
      failed += batchRecords.length
    }
    completed += batchRecords.length
    onProgress?.(completed, records.length)
  }

  // 有限并发：多个 worker 各自从共享游标领取下一个批次索引，直到取完。
  // 批次按下标预切好（start/end 由 batchIdx 算出），与执行顺序无关，
  // 所以并发执行不影响每批记录对应的序列值/进度计数是否正确。
  let nextBatchIdx = 0
  async function worker(): Promise<void> {
    while (nextBatchIdx < batchCount) {
      const batchIdx = nextBatchIdx++
      await runBatch(batchIdx)
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, batchCount) }, () => worker()))

  return { success, failed, created, updated }
}
