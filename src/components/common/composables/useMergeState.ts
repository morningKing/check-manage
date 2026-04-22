/**
 * useMergeState - 合并决策状态管理 Composable
 *
 * 管理版本合并过程中的决策状态，包括：
 * - 差异结果
 * - 用户决策（新增、删除、修改记录的选择）
 * - 展开状态管理
 *
 * 适配项目级版本管理
 */
import { reactive, computed } from 'vue'
import { mergeProjectVersion } from '@/api/projectVersion'
import type { ProjectVersion } from '@/types/version'
import type { CollectionDiff } from '@/api/projectVersion'

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
  decisions: MergeDecisions
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
    decisions: createEmptyDecisions(),
    expandedRecords: new Set<string>(),
  }
}

export function useMergeState() {
  // ==================== State ====================

  const state = reactive<MergeState>(createInitialState())

  // ==================== Computed ====================

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
   * 是否有已选择的变更（用于提交按钮状态）
   */
  const hasSelection = computed(() => {
    return (
      state.decisions.addedRecords.size > 0 ||
      state.decisions.removedRecords.size > 0 ||
      state.decisions.modifiedRecords.size > 0
    )
  })

  /**
   * 已选择的变更总数
   */
  const selectedCount = computed(() => {
    return (
      state.decisions.addedRecords.size +
      state.decisions.removedRecords.size +
      state.decisions.modifiedRecords.size
    )
  })

  /**
   * 是否有字段级决策被修改过（从默认的 source 改为 target 或反之）
   */
  const hasFieldDecisionChanged = computed(() => {
    for (const [, decision] of state.decisions.modifiedRecords) {
      for (const [, choice] of decision.fieldDecisions) {
        if (choice === 'target') return true
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
   * 设置差异结果并初始化选择
   */
  function setDiffResult(result: CollectionDiff): void {
    state.diffResult = result
    state.decisions = createEmptyDecisions()
    state.expandedRecords = new Set<string>()
  }

  /**
   * 设置整个决策对象
   */
  function setDecisions(decisions: MergeDecisions): void {
    const modifiedRecordsMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
    decisions.modifiedRecords.forEach((value, key) => {
      modifiedRecordsMap.set(key, {
        recordId: value.recordId,
        fieldDecisions: new Map(value.fieldDecisions),
      })
    })
    state.decisions = {
      addedRecords: new Set(decisions.addedRecords),
      removedRecords: new Set(decisions.removedRecords),
      modifiedRecords: modifiedRecordsMap,
    }
  }

  /**
   * 切换新增记录的选择状态
   */
  function toggleAddedRecord(recordId: string): void {
    if (state.decisions.addedRecords.has(recordId)) {
      state.decisions.addedRecords.delete(recordId)
    } else {
      state.decisions.addedRecords.add(recordId)
    }
  }

  /**
   * 切换删除记录的选择状态
   */
  function toggleRemovedRecord(recordId: string): void {
    if (state.decisions.removedRecords.has(recordId)) {
      state.decisions.removedRecords.delete(recordId)
    } else {
      state.decisions.removedRecords.add(recordId)
    }
  }

  /**
   * 切换修改记录的选择状态
   */
  function toggleModifiedRecord(recordId: string): void {
    if (state.decisions.modifiedRecords.has(recordId)) {
      state.decisions.modifiedRecords.delete(recordId)
      state.expandedRecords.delete(recordId)
    } else {
      const modifiedItem = state.diffResult?.modified?.find(m => m.id === recordId)
      if (modifiedItem) {
        const fieldDecisions = new Map<string, 'source' | 'target'>()
        modifiedItem.fields.forEach((field: any) => {
          fieldDecisions.set(field.fieldName, 'source')
        })
        state.decisions.modifiedRecords.set(recordId, {
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
    let recordDecision = state.decisions.modifiedRecords.get(recordId)
    if (!recordDecision) {
      recordDecision = {
        recordId,
        fieldDecisions: new Map(),
      }
      state.decisions.modifiedRecords.set(recordId, recordDecision)
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
   * 全选/取消全选新增记录
   */
  function selectAllAdded(selected: boolean): void {
    if (selected && state.diffResult) {
      state.decisions.addedRecords = new Set(state.diffResult.added.map((r: any) => r.id))
    } else {
      state.decisions.addedRecords = new Set()
    }
  }

  /**
   * 全选/取消全选删除记录
   */
  function selectAllRemoved(selected: boolean): void {
    if (selected && state.diffResult) {
      state.decisions.removedRecords = new Set(state.diffResult.removed.map((r: any) => r.id))
    } else {
      state.decisions.removedRecords = new Set()
    }
  }

  /**
   * 全选/取消全选修改记录
   */
  function selectAllModified(selected: boolean): void {
    if (selected && state.diffResult) {
      const newMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
      state.diffResult.modified.forEach((item: any) => {
        const fieldDecisions = new Map<string, 'source' | 'target'>()
        item.fields.forEach((field: any) => {
          fieldDecisions.set(field.fieldName, 'source')
        })
        newMap.set(item.id, { recordId: item.id, fieldDecisions })
      })
      state.decisions.modifiedRecords = newMap
    } else {
      state.decisions.modifiedRecords = new Map()
      state.expandedRecords = new Set()
    }
  }

  /**
   * 接受所有源版本变更
   */
  function acceptAllSource(): void {
    if (!state.diffResult) return

    state.diffResult.added.forEach((record: any) => {
      state.decisions.addedRecords.add(record.id)
    })

    state.diffResult.removed.forEach((record: any) => {
      state.decisions.removedRecords.add(record.id)
    })

    const newMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
    state.diffResult.modified.forEach((item: any) => {
      const fieldDecisions = new Map<string, 'source' | 'target'>()
      item.fields.forEach((field: any) => {
        fieldDecisions.set(field.fieldName, 'source')
      })
      newMap.set(item.id, { recordId: item.id, fieldDecisions })
    })
    state.decisions.modifiedRecords = newMap
  }

  /**
   * 接受所有目标版本变更
   */
  function acceptAllTarget(): void {
    if (!state.diffResult) return

    state.decisions.addedRecords = new Set()
    state.decisions.removedRecords = new Set()

    const newMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
    state.diffResult.modified.forEach((item: any) => {
      const fieldDecisions = new Map<string, 'source' | 'target'>()
      item.fields.forEach((field: any) => {
        fieldDecisions.set(field.fieldName, 'target')
      })
      newMap.set(item.id, { recordId: item.id, fieldDecisions })
    })
    state.decisions.modifiedRecords = newMap
  }

  /**
   * 为单条修改记录设置所有字段选择
   */
  function setAllFieldsForRecord(recordId: string, choice: 'source' | 'target'): void {
    const recordDecision = state.decisions.modifiedRecords.get(recordId)
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
   * 提交合并（项目级）
   */
  async function submitMerge(projectMenuId: string) {
    if (!state.sourceVersion) {
      throw new Error('Cannot build merge payload: source version not set')
    }

    // 使用 theirs 策略直接合并（简化版）
    return await mergeProjectVersion(
      state.sourceVersion.id,
      projectMenuId,
      state.targetBranch,
      'theirs'
    )
  }

  /**
   * 重置状态
   */
  function reset(): void {
    const initialState = createInitialState()
    state.sourceVersion = initialState.sourceVersion
    state.targetBranch = initialState.targetBranch
    state.diffResult = initialState.diffResult
    state.decisions = initialState.decisions
    state.expandedRecords = initialState.expandedRecords
  }

  return {
    state,
    hasDiff,
    hasSelection,
    selectedCount,
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
    submitMerge,
    reset,
  }
}