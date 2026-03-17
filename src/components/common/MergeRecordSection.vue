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
        v-for="record in filteredRecords"
        :key="record.id"
        class="record-item"
        :class="{ 'is-selected': isSelected(record.id) }"
      >
        <el-checkbox
          :model-value="isSelected(record.id)"
          @change="(val: boolean) => handleToggle(record.id, val)"
        />
        <span class="record-display">{{ getRecordDisplay(record) }}</span>
        <el-tag v-if="type === 'added' && isSelected(record.id)" type="success" size="small">
          添加
        </el-tag>
        <el-tag v-else-if="type === 'added' && !isSelected(record.id)" type="info" size="small">
          忽略
        </el-tag>
        <el-tag v-else-if="type === 'removed' && !isSelected(record.id)" type="info" size="small">
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
import { computed, ref } from 'vue'
import { CirclePlusFilled, RemoveFilled, Search } from '@element-plus/icons-vue'
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

const searchKeyword = ref('')

const showSearch = computed(() => props.records.length > 15)

const filteredRecords = computed(() => {
  if (!searchKeyword.value) return props.records
  const kw = searchKeyword.value.toLowerCase()
  return props.records.filter(record => {
    const display = getRecordDisplay(record).toLowerCase()
    return display.includes(kw)
  })
})

const SYSTEM_FIELDS = ['createTime', 'createUser', 'updateTime', 'updateUser']

const displayFields = computed(() => {
  return props.fields
    .filter(f => !f.hidden && !SYSTEM_FIELDS.includes(f.fieldName))
    .sort((a, b) => {
      // Primary key and autoSequence fields come first
      if (a.isPrimaryKey) return -1
      if (b.isPrimaryKey) return 1
      if (a.controlType === 'autoSequence') return -1
      if (b.controlType === 'autoSequence') return 1
      return a.order - b.order
    })
    .slice(0, 3)
})

function getRecordDisplay(record: Record<string, any>): string {
  const parts: string[] = []
  for (const field of displayFields.value) {
    const val = record[field.fieldName]
    if (val !== null && val !== undefined && val !== '') {
      parts.push(String(val))
    }
  }
  if (parts.length > 0) return parts.join(' | ')
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

.search-bar {
  padding: 8px 16px;
  border-bottom: 1px solid #ebeef5;
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