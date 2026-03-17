/**
 * MergeModifiedSection - 修改记录选择区块
 *
 * 职责：
 * - 展示修改记录列表
 * - 支持记录级选择
 * - 支持展开的字段级选择表格
 */
<template>
  <el-collapse-item
    v-if="records.length > 0"
    name="modified"
  >
    <template #title>
      <div class="section-header">
        <el-checkbox
          :model-value="isAllSelected"
          :indeterminate="isIndeterminate"
          @change="handleSelectAll"
          @click.stop
        />
        <el-icon class="section-icon icon-modified"><EditPen /></el-icon>
        <span class="section-title">修改记录</span>
        <el-tag type="warning" size="small">{{ records.length }}</el-tag>
        <span class="selected-count">已选 {{ selectedCount }}</span>
      </div>
    </template>

    <div v-if="showSearch" class="search-bar">
      <el-input
        v-model="searchKeyword"
        placeholder="搜索记录..."
        clearable
        size="small"
        :prefix-icon="Search"
      />
    </div>

    <div class="record-list">
      <div
        v-for="item in filteredRecords"
        :key="item.id"
        class="record-item"
        :class="{ 'is-selected': isSelected(item.id) }"
      >
        <el-checkbox
          :model-value="isSelected(item.id)"
          @change="(val: boolean) => handleToggleRecord(item.id, val)"
        />
        <span class="record-display">{{ getRecordDisplay(item) }}</span>
        <div class="record-actions">
          <el-button
            v-if="isSelected(item.id) && isExpanded(item.id)"
            text
            size="small"
            @click="handleSetAllFields(item.id, 'source')"
          >
            全用版本数据
          </el-button>
          <el-button
            v-if="isSelected(item.id) && isExpanded(item.id)"
            text
            size="small"
            @click="handleSetAllFields(item.id, 'target')"
          >
            全用当前数据
          </el-button>
          <el-button
            text
            size="small"
            @click="handleToggleExpand(item.id)"
          >
            {{ isExpanded(item.id) ? '收起' : '展开' }}
          </el-button>
        </div>

        <!-- 字段选择表格 -->
        <div v-if="isExpanded(item.id)" class="field-table">
          <div class="field-header">
            <span class="col-field">字段</span>
            <span class="col-value">版本数据</span>
            <span class="col-value">当前数据</span>
            <span class="col-action">选择</span>
          </div>
          <div
            v-for="field in item.fields"
            :key="field.fieldName"
            class="field-row"
            :class="{ 'is-auto': isAutoField(field.fieldName) }"
          >
            <span class="col-field">{{ getFieldLabel(field.fieldName) }}</span>
            <span class="col-value" :class="{ 'is-chosen': getFieldChoice(item.id, field.fieldName) === 'source' }">
              {{ formatValue(field.newValue) }}
            </span>
            <span class="col-value" :class="{ 'is-chosen': getFieldChoice(item.id, field.fieldName) === 'target' }">
              {{ formatValue(field.oldValue) }}
            </span>
            <span class="col-action">
              <template v-if="isAutoField(field.fieldName)">
                <el-tag type="info" size="small">自动</el-tag>
              </template>
              <template v-else>
                <el-radio-group
                  :model-value="getFieldChoice(item.id, field.fieldName)"
                  size="small"
                  @change="(val: 'source' | 'target') => handleFieldSelect(item.id, field.fieldName, val)"
                >
                  <el-radio-button value="source">版本</el-radio-button>
                  <el-radio-button value="target">当前</el-radio-button>
                </el-radio-group>
              </template>
            </span>
          </div>
        </div>

        <!-- 折叠状态摘要 -->
        <div v-if="isSelected(item.id) && !isExpanded(item.id)" class="collapse-summary">
          <span v-if="getSummary(item.id)">
            {{ getSummary(item.id) }}
          </span>
        </div>
      </div>
    </div>
  </el-collapse-item>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { EditPen, Search } from '@element-plus/icons-vue'
import type { FieldConfig, DiffModifiedItem } from '@/types'

interface FieldDecision {
  recordId: string
  fieldDecisions: Map<string, 'source' | 'target'>
}

interface Props {
  records: DiffModifiedItem[]
  selectedRecords: Map<string, FieldDecision>
  expandedRecords: Set<string>
  fields: FieldConfig[]
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'toggle-record', recordId: string, selected: boolean): void
  (e: 'toggle-expand', recordId: string): void
  (e: 'field-select', recordId: string, fieldName: string, choice: 'source' | 'target'): void
  (e: 'set-all-fields', recordId: string, choice: 'source' | 'target'): void
  (e: 'select-all', selected: boolean): void
}>()

const SYSTEM_FIELDS = ['createTime', 'createUser', 'updateTime', 'updateUser']

const selectedCount = computed(() => props.selectedRecords.size)

const isAllSelected = computed(() => {
  return props.records.length > 0 && props.selectedRecords.size === props.records.length
})

const isIndeterminate = computed(() => {
  return props.selectedRecords.size > 0 && props.selectedRecords.size < props.records.length
})

const searchKeyword = ref('')

const showSearch = computed(() => props.records.length > 15)

const filteredRecords = computed(() => {
  if (!searchKeyword.value) return props.records
  const kw = searchKeyword.value.toLowerCase()
  return props.records.filter(item => {
    const display = getRecordDisplay(item).toLowerCase()
    return display.includes(kw)
  })
})

const fieldLabelMap = computed(() => {
  const map: Record<string, string> = {}
  for (const f of props.fields) {
    map[f.fieldName] = f.label
  }
  return map
})

const displayFields = computed(() => {
  return props.fields
    .filter(f => !f.hidden && !SYSTEM_FIELDS.includes(f.fieldName))
    .sort((a, b) => {
      if (a.isPrimaryKey) return -1
      if (b.isPrimaryKey) return 1
      if (a.controlType === 'autoSequence') return -1
      if (b.controlType === 'autoSequence') return 1
      return a.order - b.order
    })
    .slice(0, 3)
})

function getRecordDisplay(item: DiffModifiedItem): string {
  const record = item.record || item.oldRecord || {}
  const parts: string[] = []
  for (const field of displayFields.value) {
    const val = record[field.fieldName]
    if (val !== null && val !== undefined && val !== '') {
      parts.push(String(val))
    }
  }
  if (parts.length > 0) return parts.join(' | ')
  return item.id || '未命名记录'
}

function isSelected(recordId: string): boolean {
  return props.selectedRecords.has(recordId)
}

function isExpanded(recordId: string): boolean {
  return props.expandedRecords.has(recordId)
}

function isAutoField(fieldName: string): boolean {
  return SYSTEM_FIELDS.includes(fieldName)
}

function getFieldLabel(fieldName: string): string {
  return fieldLabelMap.value[fieldName] || fieldName
}

function formatValue(val: any): string {
  if (val === null || val === undefined) return '-'
  if (Array.isArray(val)) return val.join('、')
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

function getFieldChoice(recordId: string, fieldName: string): 'source' | 'target' {
  const record = props.selectedRecords.get(recordId)
  if (record) {
    return record.fieldDecisions.get(fieldName) || 'source'
  }
  return 'source'
}

function getSummary(recordId: string): string {
  const record = props.selectedRecords.get(recordId)
  if (!record) return ''

  const modifiedItem = props.records.find(r => r.id === recordId)
  if (!modifiedItem) return ''

  const changedFieldNames = modifiedItem.fields
    .filter(f => !SYSTEM_FIELDS.includes(f.fieldName))
    .map(f => getFieldLabel(f.fieldName))

  if (changedFieldNames.length === 0) return ''
  return `变更: ${changedFieldNames.join('、')}`
}

function handleToggleRecord(recordId: string, selected: boolean): void {
  emit('toggle-record', recordId, selected)
}

function handleToggleExpand(recordId: string): void {
  emit('toggle-expand', recordId)
}

function handleFieldSelect(recordId: string, fieldName: string, choice: 'source' | 'target'): void {
  emit('field-select', recordId, fieldName, choice)
}

function handleSetAllFields(recordId: string, choice: 'source' | 'target'): void {
  emit('set-all-fields', recordId, choice)
}

function handleSelectAll(selected: boolean): void {
  emit('select-all', selected)
}
</script>

<style scoped lang="scss">
.section-header {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.section-icon {
  font-size: 18px;

  &.icon-modified {
    color: #e6a23c;
  }
}

.section-title {
  font-weight: 500;
  color: #303133;
}

.selected-count {
  margin-left: auto;
  font-size: 12px;
  color: #909399;
}

.search-bar {
  padding: 8px 16px;
  border-bottom: 1px solid #ebeef5;
}

.record-list {
  max-height: 500px;
  overflow-y: auto;
}

.record-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  transition: background-color 0.2s;

  &:last-child {
    border-bottom: none;
  }

  &:hover {
    background-color: #f5f7fa;
  }

  &.is-selected {
    background-color: #fdf6ec;
  }
}

.record-display {
  flex: 1;
  min-width: 150px;
  font-size: 14px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.record-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.field-table {
  width: 100%;
  margin-top: 12px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: hidden;
}

.field-header {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  background-color: #f5f7fa;
  font-size: 13px;
  font-weight: 500;
  color: #606266;
}

.field-row {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  border-top: 1px solid #ebeef5;

  &.is-auto {
    background-color: #fafafa;
    color: #909399;
  }
}

.col-field {
  width: 100px;
  flex-shrink: 0;
  font-weight: 500;
}

.col-value {
  flex: 1;
  min-width: 0;
  padding: 0 8px;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  &.is-chosen {
    font-weight: 500;
    color: #409eff;
  }
}

.col-action {
  width: 140px;
  flex-shrink: 0;
  text-align: right;
}

.collapse-summary {
  width: 100%;
  margin-left: 28px;
  font-size: 12px;
  color: #909399;
}
</style>