/**
 * 字段选择步骤组件
 *
 * 职责：
 * - 展示修改记录的字段级差异
 * - 支持逐字段选择源版本或目标版本
 * - 更新字段决策状态
 */
<template>
  <div class="step-field-select">
    <!-- 无修改记录提示 -->
    <el-empty
      v-if="modifiedRecords.length === 0"
      description="没有需要选择字段的修改记录"
    />

    <!-- 字段选择列表 -->
    <el-collapse v-else v-model="activePanels" class="field-collapse">
      <el-collapse-item
        v-for="item in modifiedRecords"
        :key="item.id"
        :name="item.id"
      >
        <template #title>
          <div class="collapse-title">
            <el-icon class="title-icon modified"><EditPen /></el-icon>
            <span class="title-text">{{ getRecordDisplay(item) }}</span>
            <el-tag type="warning" size="small">
              {{ item.fields.length }} 个字段变更
            </el-tag>
            <span class="selection-summary">
              已选择 {{ getSelectedFieldsCount(item.id) }}/{{ item.fields.length }} 字段
            </span>
          </div>
        </template>

        <div class="field-list">
          <!-- 字段级选择表头 -->
          <div class="field-header">
            <span class="col-field">字段</span>
            <span class="col-source">源版本</span>
            <span class="col-target">目标版本</span>
            <span class="col-action">选择</span>
          </div>

          <!-- 字段行 -->
          <div
            v-for="field in item.fields"
            :key="field.fieldName"
            class="field-row"
            :class="{ 'is-selected-source': getFieldDecision(item.id, field.fieldName) === 'source',
                      'is-selected-target': getFieldDecision(item.id, field.fieldName) === 'target' }"
          >
            <span class="col-field">
              <span class="field-label">{{ getFieldLabel(field.fieldName) }}</span>
            </span>
            <span class="col-source" :class="{ 'is-chosen': getFieldDecision(item.id, field.fieldName) === 'source' }">
              {{ formatValue(field.oldValue) }}
            </span>
            <span class="col-target" :class="{ 'is-chosen': getFieldDecision(item.id, field.fieldName) === 'target' }">
              {{ formatValue(field.newValue) }}
            </span>
            <span class="col-action">
              <el-radio-group
                :model-value="getFieldDecision(item.id, field.fieldName)"
                size="small"
                @change="(val: 'source' | 'target') => handleFieldSelect(item.id, field.fieldName, val)"
              >
                <el-radio-button value="source">
                  使用源版本
                </el-radio-button>
                <el-radio-button value="target">
                  使用目标版本
                </el-radio-button>
              </el-radio-group>
            </span>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>

    <!-- 批量操作栏 -->
    <div v-if="modifiedRecords.length > 0" class="batch-actions">
      <el-button size="small" @click="handleBatchSelectAll('source')">
        全部使用源版本
      </el-button>
      <el-button size="small" @click="handleBatchSelectAll('target')">
        全部使用目标版本
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { EditPen } from '@element-plus/icons-vue'
import type { FieldConfig, DiffResult, DiffModifiedItem } from '@/types'
import type { MergeDecisions } from '@/types/version'

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
const activePanels = ref<string[]>([])

// ==================== Computed ====================

/**
 * 获取已选择的修改记录列表
 */
const modifiedRecords = computed(() => {
  // 只显示已选择的修改记录
  return props.diffResult.modified.filter(item =>
    props.decisions.modifiedRecords.has(item.id)
  )
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
function getRecordDisplay(item: DiffModifiedItem): string {
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
 * 获取字段标签
 */
function getFieldLabel(fieldName: string): string {
  return fieldLabelMap.value[fieldName] || fieldName
}

/**
 * 格式化值显示
 */
function formatValue(val: any): string {
  if (val === null || val === undefined) return '-'
  if (Array.isArray(val)) return val.join('、')
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

/**
 * 获取字段决策
 */
function getFieldDecision(recordId: string, fieldName: string): 'source' | 'target' {
  const recordDecision = props.decisions.modifiedRecords.get(recordId)
  if (recordDecision) {
    return recordDecision.fieldDecisions.get(fieldName) || 'source'
  }
  return 'source'
}

/**
 * 获取已选择的字段数量
 */
function getSelectedFieldsCount(recordId: string): number {
  const recordDecision = props.decisions.modifiedRecords.get(recordId)
  if (recordDecision) {
    return recordDecision.fieldDecisions.size
  }
  return 0
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
 * 处理字段选择
 */
function handleFieldSelect(recordId: string, fieldName: string, value: 'source' | 'target'): void {
  const newMap = new Map(props.decisions.modifiedRecords)
  const recordDecision = newMap.get(recordId)

  if (recordDecision) {
    const newFieldDecisions = new Map(recordDecision.fieldDecisions)
    newFieldDecisions.set(fieldName, value)
    newMap.set(recordId, {
      recordId,
      fieldDecisions: newFieldDecisions
    })
  }

  updateDecisions({ modifiedRecords: newMap })
}

/**
 * 批量设置所有字段选择
 */
function handleBatchSelectAll(value: 'source' | 'target'): void {
  const newMap = new Map(props.decisions.modifiedRecords)

  for (const item of modifiedRecords.value) {
    const recordDecision = newMap.get(item.id)
    if (recordDecision) {
      const newFieldDecisions = new Map<string, 'source' | 'target'>()
      for (const field of item.fields) {
        newFieldDecisions.set(field.fieldName, value)
      }
      newMap.set(item.id, {
        recordId: item.id,
        fieldDecisions: newFieldDecisions
      })
    }
  }

  updateDecisions({ modifiedRecords: newMap })
}

// ==================== 初始化 ====================

// 初始化时展开所有已选择的修改记录
if (modifiedRecords.value.length > 0) {
  activePanels.value = modifiedRecords.value.map(item => item.id)
}
</script>

<style scoped lang="scss">
.step-field-select {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.field-collapse {
  flex: 1;
  overflow-y: auto;
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
    color: #e6a23c;
  }

  .title-text {
    font-weight: 500;
    color: #303133;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .selection-summary {
    margin-left: auto;
    font-size: 12px;
    color: #909399;
  }
}

.field-list {
  padding: 0;
}

.field-header {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  background-color: #f5f7fa;
  border-bottom: 1px solid #ebeef5;
  font-size: 13px;
  font-weight: 500;
  color: #606266;
}

.field-row {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  transition: background-color 0.2s;

  &:last-child {
    border-bottom: none;
  }

  &:hover {
    background-color: #f5f7fa;
  }

  &.is-selected-source {
    background-color: #fdf6ec;
  }

  &.is-selected-target {
    background-color: #f0f9eb;
  }
}

.col-field {
  width: 120px;
  flex-shrink: 0;
}

.field-label {
  font-weight: 500;
  color: #303133;
}

.col-source,
.col-target {
  flex: 1;
  min-width: 0;
  padding: 0 12px;
  font-size: 13px;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  &.is-chosen {
    font-weight: 500;
    color: #303133;
  }
}

.col-source.is-chosen {
  color: #e6a23c;
}

.col-target.is-chosen {
  color: #67c23a;
}

.col-action {
  width: 200px;
  flex-shrink: 0;
  text-align: right;
}

.batch-actions {
  padding: 12px 16px;
  border-top: 1px solid #ebeef5;
  background-color: #fafafa;
  display: flex;
  gap: 10px;
}
</style>