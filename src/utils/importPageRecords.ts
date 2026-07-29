import { makeImportRowId, makeStableImportId } from '@/utils/importId'

export interface ImportFailure {
  originalRecord: Record<string, any>
  payload: { id: string; data: any; relations: Record<string, string[]> }
  reason: string
}

export interface ImportPageResult {
  success: number
  failed: number
  created: number
  updated: number
  failures: ImportFailure[]
  runId?: string | null
}

export interface ImportPageParams {
  store: any // pageConfig store（解析/序列/关联方法）
  post: <T>(url: string, body: any) => Promise<T> // 注入 @/utils/request 的 post
  pageId: string
  collection: string
  records: Record<string, any>[]
  onProgress?: (current: number, total: number) => void
  onResolveProgress?: (current: number, total: number) => void
}

const BATCH_SIZE = 1000
// 并发上传批次数。批次内部分主键校验是逐条查库的（见后端 check_primary_key_unique），
// 单批太大会让单个请求变慢甚至超时；并发送多个中等大小的批次比"更大的单批"更稳妥，
// 也比纯串行快数倍。3 是留有余量的保守值（后端 waitress 线程池默认 8，DB 连接池 20）。
const CONCURRENCY = 3

/**
 * 按 BATCH_SIZE 切分、以 CONCURRENCY 个并发 worker 跑完 itemCount 个条目。
 * 每个 worker 从共享游标领取下一个批次下标，直到取完——与执行顺序无关，
 * 供 importPageRecords 和 retryImportFailures 共用，避免重复实现同一套调度逻辑。
 */
async function runBatchesConcurrently(
  itemCount: number,
  runOne: (batchIdx: number) => Promise<void>
): Promise<void> {
  const batchCount = Math.ceil(itemCount / BATCH_SIZE)
  let nextBatchIdx = 0
  async function worker(): Promise<void> {
    while (nextBatchIdx < batchCount) {
      const batchIdx = nextBatchIdx++
      await runOne(batchIdx)
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, batchCount) }, () => worker()))
}

/**
 * 单页批量导入核心：盖保序 id → 解析引用/关联 → 生成序列 → 分批 batch-create。
 * 不含任何 Vue 响应式依赖，供 DynamicPage 与批量导入编排共用。
 */
export async function importPageRecords(params: ImportPageParams): Promise<ImportPageResult> {
  const { store, post, pageId, collection, records, onProgress, onResolveProgress } = params
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

  const resolveFieldCount = store.getResolveFieldCount(pageId)
  if (resolveFieldCount > 0) {
    const resolveTotal = resolveFieldCount * records.length
    let resolveProcessed = 0
    const onChunkProcessed = (n: number): void => {
      resolveProcessed += n
      onResolveProgress?.(resolveProcessed, resolveTotal)
    }
    // 串行执行（而不是 Promise.all）：这样才能在开始前用一个稳定的 resolveTotal
    // 报告有意义的百分比；顺带消除了不同字段引用同一个尚未缓存的目标集合时，
    // 并发下可能重复发起 fetch 的小问题。
    await store.resolveRelationImportValues(pageId, records, collectionCache, onChunkProcessed)
    await store.resolveReferenceImportValues(pageId, records, collectionCache, onChunkProcessed)
    await store.resolveQuoteImportValues(pageId, records, collectionCache, onChunkProcessed)
    await store.resolveCollectionSelectImportValues(pageId, records, collectionCache, onChunkProcessed)
  }

  const sequenceValues = store.batchGenerateSequenceValues(pageId, records.length)
  const sequenceFields = Object.keys(sequenceValues)

  let success = 0, failed = 0, created = 0, updated = 0
  let completed = 0
  const failures: ImportFailure[] = []

  async function runBatch(batchIdx: number): Promise<void> {
    const start = batchIdx * BATCH_SIZE
    const end = Math.min(start + BATCH_SIZE, records.length)
    const batchRecords = records.slice(start, end)
    let batchData: Array<{ id: string; data: any; relations: Record<string, string[]> }> = []

    try {
      batchData = batchRecords.map((record, idx) => {
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

      const result = await post<{
        created: number
        updated?: number
        failed: number
        errors?: Array<{ index: number; error: string; validationErrors?: string[]; record: any }>
      }>(
        `/${collection}/batch-create`,
        { records: batchData, options: { skipValidation: false, generateSequence: true, continueOnError: true } },
      )
      success += result.created + (result.updated || 0)
      created += result.created
      updated += result.updated || 0
      failed += result.failed
      if (result.errors && result.errors.length > 0) {
        console.warn(`批次 ${batchIdx + 1} 失败记录:`, result.errors)
        for (const err of result.errors) {
          failures.push({
            originalRecord: batchRecords[err.index],
            payload: batchData[err.index],
            reason: err.validationErrors && err.validationErrors.length > 0
              ? err.validationErrors.join('; ')
              : err.error,
          })
        }
      }
    } catch (error) {
      console.error(`批次 ${batchIdx + 1} 导入失败:`, error)
      failed += batchRecords.length
      const message = error instanceof Error ? error.message : String(error)
      batchRecords.forEach((record, idx) => {
        failures.push({
          originalRecord: record,
          // batchData 构建阶段本身抛错（极少见）时兜底一个可用的最小请求体，
          // 保证即使这种边界情况也不丢失可重试性
          payload: batchData[idx] ?? {
            id: (record._importId as string) || `${collection}-${Math.random().toString(36).slice(2, 10)}`,
            data: record,
            relations: {},
          },
          reason: `请求失败：${message}，可重试`,
        })
      })
    }
    completed += batchRecords.length
    onProgress?.(completed, records.length)
  }

  await runBatchesConcurrently(records.length, runBatch)

  return { success, failed, created, updated, failures }
}

/**
 * 重试一批之前失败的记录：直接重新提交它们已经就绪的请求体
 * （不重新解析关联/引用/序列号——那些在首次导入时已经算好并塞进了 payload），
 * 按与首次导入相同的 BATCH_SIZE/CONCURRENCY 重新分批提交。
 */
export async function retryImportFailures(
  post: <T>(url: string, body: any) => Promise<T>,
  collection: string,
  failures: ImportFailure[],
  onProgress?: (current: number, total: number) => void,
): Promise<ImportPageResult> {
  let success = 0, failed = 0, created = 0, updated = 0
  let completed = 0
  const remainingFailures: ImportFailure[] = []

  async function runBatch(batchIdx: number): Promise<void> {
    const start = batchIdx * BATCH_SIZE
    const end = Math.min(start + BATCH_SIZE, failures.length)
    const batchFailures = failures.slice(start, end)
    const batchData = batchFailures.map((f) => f.payload)

    try {
      const result = await post<{
        created: number
        updated?: number
        failed: number
        errors?: Array<{ index: number; error: string; validationErrors?: string[]; record: any }>
      }>(
        `/${collection}/batch-create`,
        { records: batchData, options: { skipValidation: false, generateSequence: true, continueOnError: true } },
      )
      success += result.created + (result.updated || 0)
      created += result.created
      updated += result.updated || 0
      failed += result.failed
      if (result.errors && result.errors.length > 0) {
        for (const err of result.errors) {
          remainingFailures.push({
            originalRecord: batchFailures[err.index].originalRecord,
            payload: batchFailures[err.index].payload,
            reason: err.validationErrors && err.validationErrors.length > 0
              ? err.validationErrors.join('; ')
              : err.error,
          })
        }
      }
    } catch (error) {
      failed += batchFailures.length
      const message = error instanceof Error ? error.message : String(error)
      batchFailures.forEach((f) => {
        remainingFailures.push({
          originalRecord: f.originalRecord,
          payload: f.payload,
          reason: `请求失败：${message}，可重试`,
        })
      })
    }
    completed += batchFailures.length
    onProgress?.(completed, failures.length)
  }

  await runBatchesConcurrently(failures.length, runBatch)

  return { success, failed, created, updated, failures: remainingFailures }
}

/**
 * 计算一次重试前后的差集：哪些失败记录这次解决了（不在新的 failures 里
 * 但在旧的里），以及本次重试贡献的成功/新增/更新增量——供调用方同步到
 * /importRuns/<id>/retry-result。before/after 是同一累计口径的两个
 * ImportPageResult 快照（比如 useMultiFileImport.retryFileFailures 合并
 * 前后的 entry.result，或者调用方自己按同样的合并公式构造的一对）。
 */
export function diffRetryResult(
  before: ImportPageResult,
  after: ImportPageResult,
): { resolvedRecordIds: string[]; successDelta: number; createdDelta: number; updatedDelta: number } {
  const stillFailingIds = new Set(after.failures.map((f) => f.payload.id))
  const resolvedRecordIds = before.failures
    .map((f) => f.payload.id)
    .filter((id) => !stillFailingIds.has(id))
  return {
    resolvedRecordIds,
    successDelta: after.success - before.success,
    createdDelta: after.created - before.created,
    updatedDelta: after.updated - before.updated,
  }
}
