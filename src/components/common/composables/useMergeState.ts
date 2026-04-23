/**
 * useMergeState - 合并决策状态管理 Composable
 *
 * 管理版本合并过程中的决策状态，包括：
 * - 差异结果
 * - 用户决策（新增、删除、修改记录的选择）
 * - 展开状态管理
 *
 * 适配项目级版本管理，支持跨 collection 存储决策
 */
import { reactive, computed } from 'vue'
import { mergeProjectVersionDetailed } from '@/api/projectVersion'
import type { ProjectVersion } from '@/types/version'
import type { CollectionDiff, MergePayload, CollectionMergeDecision } from '@/api/projectVersion'

/**
 * 合并决策
 */
export interface MergeDecisions {
  addedRecords: Set<string>
  removedRecords: Set<string>
  modifiedRecords: Map<string, {
    recordId: string
    fieldDecisions: Map<string, 'source' | 'target'>
  }>
}

/**
 * 合并状态
 */
export interface MergeState {
  sourceVersion: ProjectVersion | null
  targetBranch: string
  diffResult: CollectionDiff | null
  // 按 collection 存储决策，避免切换时丢失
  collectionDecisions: Map<string, MergeDecisions>
  expandedRecords: Set<string>
}

/**
 * 创建空的决策对象
 */
function createEmptyDecisions(): MergeDecisions {
  return {
    addedRecords: new Set<string>(),
    removedRecords: new Set<string>(),
    modifiedRecords: new Map(),
  }
}

/**
 * 创建初始合并状态
 */
function createInitialState(): MergeState {
  return {
    sourceVersion: null,
    targetBranch: 'current',
    diffResult: null,
    collectionDecisions: new Map<string, MergeDecisions>(),
    expandedRecords: new Set<string>(),
  }
}

export function useMergeState() {
  // ==================== State ====================

  const state = reactive<MergeState>(createInitialState())

  // ==================== Computed ====================

  /**
   * 获取当前 collection 的决策
   */
  const currentDecisions = computed<MergeDecisions>(() => {
    const collection = state.diffResult?.collection
    if (!collection) return createEmptyDecisions()
    if (!state.collectionDecisions.has(collection)) {
      state.collectionDecisions.set(collection, createEmptyDecisions())
    }
    return state.collectionDecisions.get(collection)!
  })

  /**
   * 是否有任何差异（不关心是否已选择）
   */
  const hasDiff = computed(() => {
    if (!state.diffResult) return false
    return (
      state.diffResult.added.length > 0 ||
      state.diffResult.removed.length > 0 ||
      state.diffResult.modified.length > 0
    )
  })

  /**
   * 是否有已选择的变更（汇总所有 collection）
   */
  const hasSelection = computed(() => {
    for (const [, decisions] of state.collectionDecisions) {
      if (
        decisions.addedRecords.size > 0 ||
        decisions.removedRecords.size > 0 ||
        decisions.modifiedRecords.size > 0
      ) {
        return true
      }
    }
    return false
  })

  /**
   * 已选择的变更总数（汇总所有 collection）
   */
  const selectedCount = computed(() => {
    let total = 0
    for (const [, decisions] of state.collectionDecisions) {
      total += decisions.addedRecords.size
      total += decisions.removedRecords.size
      total += decisions.modifiedRecords.size
    }
    return total
  })

  /**
   * 当前 collection 已选择的变更数
   */
  const currentSelectedCount = computed(() => {
    const decisions = currentDecisions.value
    return (
      decisions.addedRecords.size +
      decisions.removedRecords.size +
      decisions.modifiedRecords.size
    )
  })

  /**
   * 是否有字段级决策被修改过（从默认的 source 改为 target 或反之）
   */
  const hasFieldDecisionChanged = computed(() => {
    for (const [, decisions] of state.collectionDecisions) {
      for (const [, decision] of decisions.modifiedRecords) {
        for (const [, choice] of decision.fieldDecisions) {
          if (choice === 'target') return true
        }
      }
    }
    return false
  })

  // ==================== Actions ====================

  /**
   * 设置源版本信息
   */
  function setSourceVersion(version: ProjectVersion): void {
    state.sourceVersion = version
  }

  /**
   * 设置差异结果 - 不重置已有 collection 的决策
   */
  function setDiffResult(result: CollectionDiff): void {
    state.diffResult = result
    // 只在该 collection 没有决策时初始化，保留已有决策
    if (!state.collectionDecisions.has(result.collection)) {
      state.collectionDecisions.set(result.collection, createEmptyDecisions())
    }
    state.expandedRecords = new Set<string>()
  }

  /**
   * 设置整个决策对象（用于当前 collection）
   */
  function setDecisions(decisions: MergeDecisions): void {
    const collection = state.diffResult?.collection
    if (!collection) return

    const modifiedRecordsMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
    decisions.modifiedRecords.forEach((value, key) => {
      modifiedRecordsMap.set(key, {
        recordId: value.recordId,
        fieldDecisions: new Map(value.fieldDecisions),
      })
    })
    state.collectionDecisions.set(collection, {
      addedRecords: new Set(decisions.addedRecords),
      removedRecords: new Set(decisions.removedRecords),
      modifiedRecords: modifiedRecordsMap,
    })
  }

  /**
   * 切换新增记录的选择状态
   */
  function toggleAddedRecord(recordId: string): void {
    const decisions = currentDecisions.value
    if (decisions.addedRecords.has(recordId)) {
      decisions.addedRecords.delete(recordId)
    } else {
      decisions.addedRecords.add(recordId)
    }
  }

  /**
   * 切换删除记录的选择状态
   */
  function toggleRemovedRecord(recordId: string): void {
    const decisions = currentDecisions.value
    if (decisions.removedRecords.has(recordId)) {
      decisions.removedRecords.delete(recordId)
    } else {
      decisions.removedRecords.add(recordId)
    }
  }

  /**
   * 切换修改记录的选择状态
   */
  function toggleModifiedRecord(recordId: string): void {
    const decisions = currentDecisions.value
    if (decisions.modifiedRecords.has(recordId)) {
      decisions.modifiedRecords.delete(recordId)
      state.expandedRecords.delete(recordId)
    } else {
      const modifiedItem = state.diffResult?.modified?.find(m => m.id === recordId)
      if (modifiedItem) {
        const fieldDecisions = new Map<string, 'source' | 'target'>()
        modifiedItem.fields.forEach((field: any) => {
          fieldDecisions.set(field.fieldName, 'source')
        })
        decisions.modifiedRecords.set(recordId, {
          recordId,
          fieldDecisions,
        })
        // 选中时自动展开
        state.expandedRecords.add(recordId)
      }
    }
  }

  /**
   * 设置字段决策
   */
  function setFieldDecision(
    recordId: string,
    fieldName: string,
    choice: 'source' | 'target'
  ): void {
    const decisions = currentDecisions.value
    let recordDecision = decisions.modifiedRecords.get(recordId)
    if (!recordDecision) {
      recordDecision = {
        recordId,
        fieldDecisions: new Map(),
      }
      decisions.modifiedRecords.set(recordId, recordDecision)
    }
    recordDecision.fieldDecisions.set(fieldName, choice)
  }

  /**
   * 切换修改记录的展开状态
   */
  function toggleRecordExpanded(recordId: string): void {
    if (state.expandedRecords.has(recordId)) {
      state.expandedRecords.delete(recordId)
    } else {
      state.expandedRecords.add(recordId)
    }
  }

  /**
   * 全选/取消全选新增记录（当前 collection）
   */
  function selectAllAdded(selected: boolean): void {
    const decisions = currentDecisions.value
    if (selected && state.diffResult) {
      decisions.addedRecords = new Set(state.diffResult.added.map((r: any) => r.id))
    } else {
      decisions.addedRecords = new Set()
    }
  }

  /**
   * 全选/取消全选删除记录（当前 collection）
   */
  function selectAllRemoved(selected: boolean): void {
    const decisions = currentDecisions.value
    if (selected && state.diffResult) {
      decisions.removedRecords = new Set(state.diffResult.removed.map((r: any) => r.id))
    } else {
      decisions.removedRecords = new Set()
    }
  }

  /**
   * 全选/取消全选修改记录（当前 collection）
   */
  function selectAllModified(selected: boolean): void {
    const decisions = currentDecisions.value
    if (selected && state.diffResult) {
      const newMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
      state.diffResult.modified.forEach((item: any) => {
        const fieldDecisions = new Map<string, 'source' | 'target'>()
        item.fields.forEach((field: any) => {
          fieldDecisions.set(field.fieldName, 'source')
        })
        newMap.set(item.id, { recordId: item.id, fieldDecisions })
      })
      decisions.modifiedRecords = newMap
    } else {
      decisions.modifiedRecords = new Map()
      state.expandedRecords = new Set()
    }
  }

  /**
   * 接受所有源版本变更（当前 collection）
   */
  function acceptAllSource(): void {
    if (!state.diffResult) return
    const decisions = currentDecisions.value

    state.diffResult.added.forEach((record: any) => {
      decisions.addedRecords.add(record.id)
    })

    state.diffResult.removed.forEach((record: any) => {
      decisions.removedRecords.add(record.id)
    })

    const newMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
    state.diffResult.modified.forEach((item: any) => {
      const fieldDecisions = new Map<string, 'source' | 'target'>()
      item.fields.forEach((field: any) => {
        fieldDecisions.set(field.fieldName, 'source')
      })
      newMap.set(item.id, { recordId: item.id, fieldDecisions })
    })
    decisions.modifiedRecords = newMap
  }

  /**
   * 接受所有目标版本变更（当前 collection）
   */
  function acceptAllTarget(): void {
    if (!state.diffResult) return
    const decisions = currentDecisions.value

    decisions.addedRecords = new Set()
    decisions.removedRecords = new Set()

    const newMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
    state.diffResult.modified.forEach((item: any) => {
      const fieldDecisions = new Map<string, 'source' | 'target'>()
      item.fields.forEach((field: any) => {
        fieldDecisions.set(field.fieldName, 'target')
      })
      newMap.set(item.id, { recordId: item.id, fieldDecisions })
    })
    decisions.modifiedRecords = newMap
  }

  /**
   * 为单条修改记录设置所有字段选择
   */
  function setAllFieldsForRecord(recordId: string, choice: 'source' | 'target'): void {
    const decisions = currentDecisions.value
    const recordDecision = decisions.modifiedRecords.get(recordId)
    if (!recordDecision) return

    const modifiedItem = state.diffResult?.modified?.find((m: any) => m.id === recordId)
    if (!modifiedItem) return

    const newFieldDecisions = new Map<string, 'source' | 'target'>()
    modifiedItem.fields.forEach((field: any) => {
      newFieldDecisions.set(field.fieldName, choice)
    })
    recordDecision.fieldDecisions = newFieldDecisions
  }

  /**
   * 构建合并请求 payload（包含所有 collection 的决策）
   */
  function buildMergePayload(): MergePayload | null {
    if (!state.sourceVersion) return null

    const collections: CollectionMergeDecision[] = []

    for (const [collectionName, decisions] of state.collectionDecisions) {
      if (
        decisions.addedRecords.size > 0 ||
        decisions.removedRecords.size > 0 ||
        decisions.modifiedRecords.size > 0
      ) {
        const modified: MergePayload['collections'][0]['modified'] = []
        for (const [recordId, rd] of decisions.modifiedRecords) {
          const fieldDecisions: { fieldName: string; useSource: boolean }[] = []
          for (const [fieldName, choice] of rd.fieldDecisions) {
            fieldDecisions.push({
              fieldName,
              useSource: choice === 'source',
            })
          }
          modified.push({
            recordId,
            fieldDecisions,
          })
        }

        collections.push({
          collection: collectionName,
          added: Array.from(decisions.addedRecords),
          removed: Array.from(decisions.removedRecords),
          modified,
        })
      }
    }

    if (collections.length === 0) return null

    return {
      versionId: state.sourceVersion.id,
      targetBranch: state.targetBranch,
      collections,
    }
  }

  /**
   * 提交合并（项目级，支持详细决策）
   */
  async function submitMerge(projectMenuId: string) {
    if (!state.sourceVersion) {
      throw new Error('Cannot build merge payload: source version not set')
    }

    const payload = buildMergePayload()
    if (!payload) {
      throw new Error('No selections made for merge')
    }

    // 使用详细合并 API
    return await mergeProjectVersionDetailed(payload, projectMenuId)
  }

  /**
   * 重置状态
   */
  function reset(): void {
    const initialState = createInitialState()
    state.sourceVersion = initialState.sourceVersion
    state.targetBranch = initialState.targetBranch
    state.diffResult = initialState.diffResult
    state.collectionDecisions = initialState.collectionDecisions
    state.expandedRecords = initialState.expandedRecords
  }

  return {
    state,
    currentDecisions,
    hasDiff,
    hasSelection,
    selectedCount,
    currentSelectedCount,
    hasFieldDecisionChanged,
    setSourceVersion,
    setDiffResult,
    setDecisions,
    toggleAddedRecord,
    toggleRemovedRecord,
    toggleModifiedRecord,
    setFieldDecision,
    toggleRecordExpanded,
    selectAllAdded,
    selectAllRemoved,
    selectAllModified,
    acceptAllSource,
    acceptAllTarget,
    setAllFieldsForRecord,
    buildMergePayload,
    submitMerge,
    reset,
  }
}