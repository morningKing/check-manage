/**
 * 记录选择步骤组件
 *
 * 职责：
 * - 展示新增、删除、修改三类记录列表
 * - 支持批量选择/取消选择记录
 * - 更新合并决策状态
 */
<template>
  <div class="step-record-select">
    <!-- 无变更提示 -->
    <el-empty
      v-if="!hasAnyChanges"
      description="没有需要选择的记录变更"
    />

    <!-- 记录列表 -->
    <el-collapse v-else v-model="activePanels" class="record-collapse">
      <!-- 新增记录 -->
      <el-collapse-item
        v-if="diffResult.added.length > 0"
        name="added"
      >
        <template #title>
          <div class="collapse-title">
            <el-checkbox
              :model-value="isAllAddedSelected"
              :indeterminate="isAddedIndeterminate"
              @change="handleSelectAllAdded"
              @click.stop
            />
            <el-icon class="title-icon added"><CirclePlusFilled /></el-icon>
            <span class="title-text">新增记录</span>
            <el-tag type="success" size="small">{{ diffResult.added.length }}</el-tag>
            <span class="selected-count">已选 {{ selectedAddedCount }}</span>
          </div>
        </template>

        <div class="record-list">
          <div
            v-for="record in diffResult.added"
            :key="record.id"
            class="record-item"
          >
            <el-checkbox
              :model-value="decisions.addedRecords.has(record.id)"
              @change="(val: boolean) => handleToggleAdded(record.id, val)"
            />
            <span class="record-display">{{ getRecordDisplay(record) }}</span>
          </div>
        </div>
      </el-collapse-item>

      <!-- 删除记录 -->
      <el-collapse-item
        v-if="diffResult.removed.length > 0"
        name="removed"
      >
        <template #title>
          <div class="collapse-title">
            <el-checkbox
              :model-value="isAllRemovedSelected"
              :indeterminate="isRemovedIndeterminate"
              @change="handleSelectAllRemoved"
              @click.stop
            />
            <el-icon class="title-icon removed"><RemoveFilled /></el-icon>
            <span class="title-text">删除记录</span>
            <el-tag type="danger" size="small">{{ diffResult.removed.length }}</el-tag>
            <span class="selected-count">已选 {{ selectedRemovedCount }}</span>
          </div>
        </template>

        <div class="record-list">
          <div
            v-for="record in diffResult.removed"
            :key="record.id"
            class="record-item"
          >
            <el-checkbox
              :model-value="decisions.removedRecords.has(record.id)"
              @change="(val: boolean) => handleToggleRemoved(record.id, val)"
            />
            <span class="record-display">{{ getRecordDisplay(record) }}</span>
          </div>
        </div>
      </el-collapse-item>

      <!-- 修改记录 -->
      <el-collapse-item
        v-if="diffResult.modified.length > 0"
        name="modified"
      >
        <template #title>
          <div class="collapse-title">
            <el-checkbox
              :model-value="isAllModifiedSelected"
              :indeterminate="isModifiedIndeterminate"
              @change="handleSelectAllModified"
              @click.stop
            />
            <el-icon class="title-icon modified"><EditPen /></el-icon>
            <span class="title-text">修改记录</span>
            <el-tag type="warning" size="small">{{ diffResult.modified.length }}</el-tag>
            <span class="selected-count">已选 {{ selectedModifiedCount }}</span>
          </div>
        </template>

        <div class="record-list">
          <div
            v-for="item in diffResult.modified"
            :key="item.id"
            class="record-item modified-item"
          >
            <el-checkbox
              :model-value="decisions.modifiedRecords.has(item.id)"
              @change="(val: boolean) => handleToggleModified(item.id, val)"
            />
            <div class="modified-content">
              <span class="record-display">{{ getModifiedRecordDisplay(item) }}</span>
              <div class="changed-fields">
                <el-tag
                  v-for="fieldName in getChangedFieldNames(item)"
                  :key="fieldName"
                  size="small"
                  type="info"
                >
                  {{ getFieldLabel(fieldName) }}
                </el-tag>
              </div>
            </div>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { CirclePlusFilled, RemoveFilled, EditPen } from '@element-plus/icons-vue'
import type { FieldConfig, DiffResult, DiffModifiedItem } from '@/types'

// ==================== 类型定义 ====================

/**
 * 合并决策状态
 */
export interface MergeDecisions {
  addedRecords: Set<string>
  removedRecords: Set<string>
  modifiedRecords: Map<string, {
    recordId: string
    fieldDecisions: Map<string, 'source' | 'target'>
  }>
}

// ==================== Props & Emits ====================

interface Props {
  diffResult: DiffResult
  fields: FieldConfig[]
  decisions: MergeDecisions
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:decisions', value: MergeDecisions): void
}>()

// ==================== State ====================

/**
 * 展开的折叠面板
 */
const activePanels = ref<string[]>(['added', 'removed', 'modified'])

// ==================== Computed ====================

/**
 * 是否有任何变更
 */
const hasAnyChanges = computed(() => {
  return (
    props.diffResult.added.length > 0 ||
    props.diffResult.removed.length > 0 ||
    props.diffResult.modified.length > 0
  )
})

/**
 * 新增记录选择数量
 */
const selectedAddedCount = computed(() => props.decisions.addedRecords.size)

/**
 * 删除记录选择数量
 */
const selectedRemovedCount = computed(() => props.decisions.removedRecords.size)

/**
 * 修改记录选择数量
 */
const selectedModifiedCount = computed(() => props.decisions.modifiedRecords.size)

/**
 * 是否全选新增记录
 */
const isAllAddedSelected = computed(() => {
  return props.diffResult.added.length > 0 &&
    props.decisions.addedRecords.size === props.diffResult.added.length
})

/**
 * 新增记录是否部分选择
 */
const isAddedIndeterminate = computed(() => {
  return props.decisions.addedRecords.size > 0 &&
    props.decisions.addedRecords.size < props.diffResult.added.length
})

/**
 * 是否全选删除记录
 */
const isAllRemovedSelected = computed(() => {
  return props.diffResult.removed.length > 0 &&
    props.decisions.removedRecords.size === props.diffResult.removed.length
})

/**
 * 删除记录是否部分选择
 */
const isRemovedIndeterminate = computed(() => {
  return props.decisions.removedRecords.size > 0 &&
    props.decisions.removedRecords.size < props.diffResult.removed.length
})

/**
 * 是否全选修改记录
 */
const isAllModifiedSelected = computed(() => {
  return props.diffResult.modified.length > 0 &&
    props.decisions.modifiedRecords.size === props.diffResult.modified.length
})

/**
 * 修改记录是否部分选择
 */
const isModifiedIndeterminate = computed(() => {
  return props.decisions.modifiedRecords.size > 0 &&
    props.decisions.modifiedRecords.size < props.diffResult.modified.length
})

/**
 * 字段标签映射
 */
const fieldLabelMap = computed(() => {
  const map: Record<string, string> = {}
  for (const f of props.fields) {
    map[f.fieldName] = f.label
  }
  return map
})

/**
 * 主显示字段（第一个文本字段或主键字段）
 */
const displayField = computed(() => {
  // 优先查找主键字段
  const pkField = props.fields.find(f => f.isPrimaryKey)
  if (pkField) return pkField

  // 其次查找自增序列字段
  const seqField = props.fields.find(f => f.controlType === 'autoSequence')
  if (seqField) return seqField

  // 最后查找第一个文本字段
  const textField = props.fields
    .filter(f => ['text', 'textarea'].includes(f.controlType))
    .sort((a, b) => a.order - b.order)[0]

  return textField
})

// ==================== Methods ====================

/**
 * 获取记录显示值
 */
function getRecordDisplay(record: Record<string, any>): string {
  if (displayField.value) {
    const val = record[displayField.value.fieldName]
    if (val !== null && val !== undefined && val !== '') {
      return String(val)
    }
  }
  return record.id || '未命名记录'
}

/**
 * 获取修改记录显示值
 */
function getModifiedRecordDisplay(item: DiffModifiedItem): string {
  const record = item.record || item.oldRecord || {}
  if (displayField.value) {
    const val = record[displayField.value.fieldName]
    if (val !== null && val !== undefined && val !== '') {
      return String(val)
    }
  }
  return item.id || '未命名记录'
}

/**
 * 获取变更字段名列表
 */
function getChangedFieldNames(item: DiffModifiedItem): string[] {
  return item.fields?.map(f => f.fieldName) || []
}

/**
 * 获取字段标签
 */
function getFieldLabel(fieldName: string): string {
  return fieldLabelMap.value[fieldName] || fieldName
}

/**
 * 更新决策状态
 */
function updateDecisions(updates: Partial<MergeDecisions>): void {
  emit('update:decisions', {
    ...props.decisions,
    ...updates
  })
}

/**
 * 切换新增记录选择
 */
function handleToggleAdded(recordId: string, selected: boolean): void {
  const newSet = new Set(props.decisions.addedRecords)
  if (selected) {
    newSet.add(recordId)
  } else {
    newSet.delete(recordId)
  }
  updateDecisions({ addedRecords: newSet })
}

/**
 * 切换删除记录选择
 */
function handleToggleRemoved(recordId: string, selected: boolean): void {
  const newSet = new Set(props.decisions.removedRecords)
  if (selected) {
    newSet.add(recordId)
  } else {
    newSet.delete(recordId)
  }
  updateDecisions({ removedRecords: newSet })
}

/**
 * 切换修改记录选择
 */
function handleToggleModified(recordId: string, selected: boolean): void {
  const newMap = new Map(props.decisions.modifiedRecords)
  if (selected) {
    // 初始化字段决策，默认选择源版本
    const fieldDecisions = new Map<string, 'source' | 'target'>()
    const modifiedItem = props.diffResult.modified.find(m => m.id === recordId)
    if (modifiedItem?.fields) {
      for (const f of modifiedItem.fields) {
        fieldDecisions.set(f.fieldName, 'source')
      }
    }
    newMap.set(recordId, { recordId, fieldDecisions })
  } else {
    newMap.delete(recordId)
  }
  updateDecisions({ modifiedRecords: newMap })
}

/**
 * 全选/取消全选新增记录
 */
function handleSelectAllAdded(selected: boolean): void {
  const newSet = new Set<string>()
  if (selected) {
    for (const record of props.diffResult.added) {
      newSet.add(record.id)
    }
  }
  updateDecisions({ addedRecords: newSet })
}

/**
 * 全选/取消全选删除记录
 */
function handleSelectAllRemoved(selected: boolean): void {
  const newSet = new Set<string>()
  if (selected) {
    for (const record of props.diffResult.removed) {
      newSet.add(record.id)
    }
  }
  updateDecisions({ removedRecords: newSet })
}

/**
 * 全选/取消全选修改记录
 */
function handleSelectAllModified(selected: boolean): void {
  const newMap = new Map<string, { recordId: string; fieldDecisions: Map<string, 'source' | 'target'> }>()
  if (selected) {
    for (const item of props.diffResult.modified) {
      const fieldDecisions = new Map<string, 'source' | 'target'>()
      if (item.fields) {
        for (const f of item.fields) {
          fieldDecisions.set(f.fieldName, 'source')
        }
      }
      newMap.set(item.id, { recordId: item.id, fieldDecisions })
    }
  }
  updateDecisions({ modifiedRecords: newMap })
}

// ==================== 初始化默认展开 ====================

watch(() => props.diffResult, (result) => {
  if (result) {
    // 只展开有数据的面板
    const panels: string[] = []
    if (result.added.length > 0) panels.push('added')
    if (result.removed.length > 0) panels.push('removed')
    if (result.modified.length > 0) panels.push('modified')
    activePanels.value = panels
  }
}, { immediate: true })
</script>

<style scoped lang="scss">
.step-record-select {
  height: 100%;
  overflow-y: auto;
}

.record-collapse {
  border: none;

  :deep(.el-collapse-item__header) {
    height: auto;
    padding: 12px 16px;
    background-color: #fafafa;
    border-bottom: 1px solid #ebeef5;

    &:hover {
      background-color: #f5f7fa;
    }
  }

  :deep(.el-collapse-item__wrap) {
    border-bottom: none;
  }

  :deep(.el-collapse-item__content) {
    padding: 0;
  }
}

.collapse-title {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;

  .title-icon {
    font-size: 18px;

    &.added {
      color: #67c23a;
    }

    &.removed {
      color: #f56c6c;
    }

    &.modified {
      color: #e6a23c;
    }
  }

  .title-text {
    font-weight: 500;
    color: #303133;
  }

  .selected-count {
    margin-left: auto;
    font-size: 12px;
    color: #909399;
  }
}

.record-list {
  max-height: 300px;
  overflow-y: auto;
}

.record-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid #f0f0f0;
  transition: background-color 0.2s;

  &:last-child {
    border-bottom: none;
  }

  &:hover {
    background-color: #f5f7fa;
  }

  &.modified-item {
    align-items: flex-start;
  }
}

.record-display {
  flex: 1;
  font-size: 14px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modified-content {
  flex: 1;
  min-width: 0;
}

.changed-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
</style>