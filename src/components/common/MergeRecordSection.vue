/**
 * MergeRecordSection - 新增/删除记录选择区块
 *
 * 职责：
 * - 展示新增或删除记录列表
 * - 支持单条记录选择
 * - 支持 Section 级别全选
 */
<template>
  <el-collapse-item
    v-if="records.length > 0"
    :name="name"
  >
    <template #title>
      <div class="section-header">
        <el-checkbox
          :model-value="isAllSelected"
          @change="handleSelectAll"
          @click.stop
        />
        <el-icon class="section-icon" :class="iconClass">
          <component :is="icon" />
        </el-icon>
        <span class="section-title">{{ title }}</span>
        <el-tag :type="tagType" size="small">{{ records.length }}</el-tag>
        <span class="selected-count">已选 {{ selectedCount }}</span>
      </div>
    </template>

    <div class="record-list">
      <div
        v-for="record in records"
        :key="record.id"
        class="record-item"
        :class="{ 'is-selected': isSelected(record.id) }"
      >
        <el-checkbox
          :model-value="isSelected(record.id)"
          @change="(val: boolean) => handleToggle(record.id, val)"
        />
        <span class="record-display">{{ getRecordDisplay(record) }}</span>
        <el-tag v-if="type === 'removed' && !isSelected(record.id)" type="info" size="small">
          保留
        </el-tag>
        <el-tag v-else-if="type === 'removed' && isSelected(record.id)" type="danger" size="small">
          删除
        </el-tag>
      </div>
    </div>
  </el-collapse-item>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { CirclePlusFilled, RemoveFilled } from '@element-plus/icons-vue'
import type { FieldConfig } from '@/types'

interface Props {
  type: 'added' | 'removed'
  title: string
  name: string
  records: Record<string, any>[]
  selectedIds: Set<string>
  fields: FieldConfig[]
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'toggle', recordId: string, selected: boolean): void
  (e: 'select-all', selected: boolean): void
}>()

const icon = computed(() => props.type === 'added' ? CirclePlusFilled : RemoveFilled)

const iconClass = computed(() => props.type === 'added' ? 'icon-added' : 'icon-removed')

const tagType = computed(() => props.type === 'added' ? 'success' : 'danger')

const selectedCount = computed(() => props.selectedIds.size)

const isAllSelected = computed(() => {
  return props.records.length > 0 && props.selectedIds.size === props.records.length
})

const displayField = computed(() => {
  const pkField = props.fields.find(f => f.isPrimaryKey)
  if (pkField) return pkField

  const seqField = props.fields.find(f => f.controlType === 'autoSequence')
  if (seqField) return seqField

  const textField = props.fields
    .filter(f => ['text', 'textarea'].includes(f.controlType))
    .sort((a, b) => a.order - b.order)[0]

  return textField
})

function getRecordDisplay(record: Record<string, any>): string {
  if (displayField.value) {
    const val = record[displayField.value.fieldName]
    if (val !== null && val !== undefined && val !== '') {
      return String(val)
    }
  }
  return record.id || '未命名记录'
}

function isSelected(recordId: string): boolean {
  return props.selectedIds.has(recordId)
}

function handleToggle(recordId: string, selected: boolean): void {
  emit('toggle', recordId, selected)
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

  &.icon-added {
    color: #67c23a;
  }

  &.icon-removed {
    color: #f56c6c;
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

  &.is-selected {
    background-color: var(--el-color-primary-light-9);
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
</style>