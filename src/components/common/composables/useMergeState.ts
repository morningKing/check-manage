/**
 * useMergeState - 合并决策状态管理 Composable
 *
 * 管理版本合并过程中的决策状态，包括：
 * - 步骤导航
 * - 差异结果
 * - 用户决策（新增、删除、修改记录的选择）
 */
import { reactive, computed } from 'vue'
import { partialMergeVersion } from '@/api/version'
import type {
  MergeState,
  MergeStep,
  MergeDecisions,
  CollectionVersion,
  PartialMergeRequest,
  PartialMergeDecisions,
  ModifiedRecordDecision,
} from '@/types/version'
import type { DiffResult } from '@/types/backup'

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
    step: 'overview',
    sourceVersion: null,
    targetBranch: 'current',
    diffResult: null,
    decisions: createEmptyDecisions(),
  }
}

export function useMergeState() {
  // ==================== State ====================

  const state = reactive<MergeState>(createInitialState())

  // ==================== Computed ====================

  /**
   * 是否有任何更改
   */
  const hasChanges = computed(() => {
    return (
      state.decisions.addedRecords.size > 0 ||
      state.decisions.removedRecords.size > 0 ||
      state.decisions.modifiedRecords.size > 0
    )
  })

  /**
   * 是否可以提交（与 hasChanges 相同，用于 UI 绑定）
   */
  const canSubmit = computed(() => hasChanges.value)

  /**
   * 修改记录数量
   */
  const modifiedRecordCount = computed(() => {
    return state.diffResult?.modified?.length ?? 0
  })

  /**
   * 新增记录数量
   */
  const addedRecordCount = computed(() => {
    return state.diffResult?.added?.length ?? 0
  })

  /**
   * 删除记录数量
   */
  const removedRecordCount = computed(() => {
    return state.diffResult?.removed?.length ?? 0
  })

  /**
   * 已选择的新增记录数量
   */
  const selectedAddedCount = computed(() => {
    return state.decisions.addedRecords.size
  })

  /**
   * 已选择的删除记录数量
   */
  const selectedRemovedCount = computed(() => {
    return state.decisions.removedRecords.size
  })

  /**
   * 已选择的修改记录数量
   */
  const selectedModifiedCount = computed(() => {
    return state.decisions.modifiedRecords.size
  })

  // ==================== Actions ====================

  /**
   * 设置步骤
   */
  function setStep(step: MergeStep): void {
    state.step = step
  }

  /**
   * 设置源版本信息
   */
  function setSourceVersion(version: CollectionVersion): void {
    state.sourceVersion = version
  }

  /**
   * 设置目标分支
   */
  function setTargetBranch(branch: string): void {
    state.targetBranch = branch
  }

  /**
   * 设置差异结果并初始化选择
   */
  function setDiffResult(result: DiffResult): void {
    state.diffResult = result
    // 重置决策
    state.decisions = createEmptyDecisions()
  }

  /**
   * 设置整个决策对象
   * 用于子组件更新决策时同步状态
   * 注意：深拷贝嵌套的 Map 结构，避免共享引用
   */
  function setDecisions(decisions: MergeDecisions): void {
    const modifiedRecordsMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
    decisions.modifiedRecords.forEach((value, key) => {
      modifiedRecordsMap.set(key, {
        recordId: value.recordId,
        fieldDecisions: new Map(value.fieldDecisions), // Deep copy nested Map
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
   * 设置修改记录的字段决策
   * @param recordId 记录 ID
   * @param fieldName 字段名
   * @param choice 'source' 使用源版本值，'target' 使用目标版本值
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
   * 设置整个修改记录的决策
   * @param recordId 记录 ID
   * @param fieldChoices 字段选择映射
   */
  function setRecordDecision(
    recordId: string,
    fieldChoices: Map<string, 'source' | 'target'>
  ): void {
    state.decisions.modifiedRecords.set(recordId, {
      recordId,
      fieldDecisions: fieldChoices,
    })
  }

  /**
   * 切换修改记录的选择状态
   * 如果记录已选择，则移除；否则添加并使用源版本作为默认值
   */
  function toggleModifiedRecord(recordId: string): void {
    if (state.decisions.modifiedRecords.has(recordId)) {
      state.decisions.modifiedRecords.delete(recordId)
    } else {
      // 添加记录，并初始化所有字段使用源版本
      const modifiedItem = state.diffResult?.modified?.find(m => m.id === recordId)
      if (modifiedItem) {
        const fieldDecisions = new Map<string, 'source' | 'target'>()
        modifiedItem.fields.forEach(field => {
          fieldDecisions.set(field.fieldName, 'source')
        })
        state.decisions.modifiedRecords.set(recordId, {
          recordId,
          fieldDecisions,
        })
      }
    }
  }

  /**
   * 接受所有源版本变更
   * - 新增记录：全部选择
   * - 删除记录：全部选择
   * - 修改记录：全部选择，所有字段使用源版本值
   */
  function acceptAllSource(): void {
    if (!state.diffResult) return

    // 新增记录：全部选择
    state.diffResult.added.forEach(record => {
      const id = record.id
      if (id) {
        state.decisions.addedRecords.add(id)
      }
    })

    // 删除记录：全部选择
    state.diffResult.removed.forEach(record => {
      const id = record.id
      if (id) {
        state.decisions.removedRecords.add(id)
      }
    })

    // 修改记录：全部选择，字段使用源版本值
    state.diffResult.modified.forEach(item => {
      const fieldDecisions = new Map<string, 'source' | 'target'>()
      item.fields.forEach(field => {
        fieldDecisions.set(field.fieldName, 'source')
      })
      state.decisions.modifiedRecords.set(item.id, {
        recordId: item.id,
        fieldDecisions,
      })
    })
  }

  /**
   * 接受所有目标版本（拒绝源版本变更）
   * - 新增记录：全部不选择（不合并）
   * - 删除记录：全部不选择（保留）
   * - 修改记录：全部选择，所有字段使用目标版本值
   */
  function acceptAllTarget(): void {
    if (!state.diffResult) return

    // 新增记录：全部不选择（清空）
    state.decisions.addedRecords.clear()

    // 删除记录：全部不选择（清空）
    state.decisions.removedRecords.clear()

    // 修改记录：全部选择，字段使用目标版本值
    state.diffResult.modified.forEach(item => {
      const fieldDecisions = new Map<string, 'source' | 'target'>()
      item.fields.forEach(field => {
        fieldDecisions.set(field.fieldName, 'target')
      })
      state.decisions.modifiedRecords.set(item.id, {
        recordId: item.id,
        fieldDecisions,
      })
    })
  }

  /**
   * 构建合并请求负载
   */
  function buildMergePayload(): PartialMergeRequest | null {
    if (!state.sourceVersion) {
      return null
    }

    // 转换修改记录的决策格式
    const modifiedRecords: ModifiedRecordDecision[] = []

    state.decisions.modifiedRecords.forEach((decision, recordId) => {
      if (!state.diffResult) return

      const modifiedItem = state.diffResult.modified.find(m => m.id === recordId)
      if (!modifiedItem) return

      const fieldValues: Record<string, any> = {}

      decision.fieldDecisions.forEach((choice, fieldName) => {
        const field = modifiedItem.fields.find(f => f.fieldName === fieldName)
        if (field) {
          // source: 使用源版本值（newValue），target: 使用目标版本值（oldValue）
          fieldValues[fieldName] = choice === 'source' ? field.newValue : field.oldValue
        }
      })

      modifiedRecords.push({
        record_id: recordId,
        field_values: fieldValues,
      })
    })

    const decisions: PartialMergeDecisions = {
      added_record_ids: Array.from(state.decisions.addedRecords),
      removed_record_ids: Array.from(state.decisions.removedRecords),
      modified_records: modifiedRecords,
    }

    return {
      source_version_id: state.sourceVersion.id,
      target_branch: state.targetBranch,
      decisions,
    }
  }

  /**
   * 提交合并
   */
  async function submitMerge() {
    const payload = buildMergePayload()
    if (!payload) {
      throw new Error('Cannot build merge payload: source version not set')
    }
    return await partialMergeVersion(payload)
  }

  /**
   * 重置状态
   */
  function reset(): void {
    const initialState = createInitialState()
    state.step = initialState.step
    state.sourceVersion = initialState.sourceVersion
    state.targetBranch = initialState.targetBranch
    state.diffResult = initialState.diffResult
    state.decisions = initialState.decisions
  }

  return {
    // State (通过 setter 方法修改，不使用 readonly 避免类型嵌套问题)
    state,

    // Computed
    hasChanges,
    canSubmit,
    modifiedRecordCount,
    addedRecordCount,
    removedRecordCount,
    selectedAddedCount,
    selectedRemovedCount,
    selectedModifiedCount,

    // Actions
    setStep,
    setSourceVersion,
    setTargetBranch,
    setDiffResult,
    setDecisions,
    toggleAddedRecord,
    toggleRemovedRecord,
    setFieldDecision,
    setRecordDecision,
    toggleModifiedRecord,
    acceptAllSource,
    acceptAllTarget,
    buildMergePayload,
    submitMerge,
    reset,
  }
}