<template>
  <div
    class="page-item"
    :class="{ active, selected }"
    @click="$emit('click')"
  >
    <el-checkbox
      :model-value="selected"
      @click.stop
      @change="(val: boolean) => $emit('toggle-select', val)"
      class="page-item-check"
    />

    <div class="page-info">
      <div class="page-name-row">
        <span class="page-name" :title="config.name">{{ config.name }}</span>
        <div class="page-badges">
          <el-tag v-if="config.apiPublic" type="success" size="small" effect="plain">API</el-tag>
          <el-tag v-if="hasKanban" type="primary" size="small" effect="plain">看板</el-tag>
          <el-tag v-if="hasCalendar" type="warning" size="small" effect="plain">日历</el-tag>
          <el-tag v-if="config.validationScript" size="small" effect="plain" style="--el-tag-bg-color: #f3e8ff; --el-tag-text-color: #7c3aed; --el-tag-border-color: #ddd6fe">校验</el-tag>
          <el-tag v-if="isOrphan" type="info" size="small" effect="plain">未引用</el-tag>
        </div>
      </div>
      <div class="page-meta">
        <span>{{ config.fields.length }} 字段</span>
        <span v-if="config.updatedAt" class="meta-sep">·</span>
        <span v-if="config.updatedAt">{{ formatTime(config.updatedAt) }}</span>
      </div>
    </div>

    <div class="page-actions">
      <el-tooltip content="复制" placement="top">
        <el-button
          type="primary"
          link
          size="small"
          @click.stop="$emit('duplicate')"
        >
          <el-icon><CopyDocument /></el-icon>
        </el-button>
      </el-tooltip>
      <el-tooltip content="删除" placement="top">
        <el-button
          type="danger"
          link
          size="small"
          @click.stop="$emit('delete')"
        >
          <el-icon><Delete /></el-icon>
        </el-button>
      </el-tooltip>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { CopyDocument, Delete } from '@element-plus/icons-vue'
import type { PageConfig } from '@/types'

const props = defineProps<{
  config: PageConfig
  active: boolean
  selected: boolean
  isOrphan: boolean
}>()

defineEmits<{
  (e: 'click'): void
  (e: 'toggle-select', value: boolean): void
  (e: 'duplicate'): void
  (e: 'delete'): void
}>()

const hasKanban = computed(() => !!props.config.viewConfig?.kanban?.groupField)
const hasCalendar = computed(() => !!props.config.viewConfig?.calendar?.dateField)

function formatTime(value: string): string {
  if (!value) return ''
  try {
    const d = new Date(value)
    if (isNaN(d.getTime())) return ''
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${m}-${day}`
  } catch {
    return ''
  }
}
</script>

<style scoped lang="scss">
.page-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
  background: var(--el-bg-color);

  &:hover {
    border-color: var(--el-color-primary);
    background-color: var(--el-fill-color-light);

    .page-actions {
      opacity: 1;
    }
  }

  &.active {
    border-color: var(--el-color-primary);
    background-color: var(--el-color-primary-light-9);
  }

  &.selected {
    border-color: var(--el-color-success);
    background-color: var(--el-color-success-light-9);
  }

  &.selected.active {
    border-color: var(--el-color-primary);
    background-color: var(--el-color-primary-light-9);
  }
}

.page-item-check {
  flex-shrink: 0;
}

.page-info {
  flex: 1;
  min-width: 0;

  .page-name-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
  }

  .page-name {
    font-weight: 500;
    color: var(--el-text-color-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }

  .page-badges {
    display: flex;
    gap: 4px;
    flex-shrink: 0;

    :deep(.el-tag) {
      padding: 0 6px;
      height: 18px;
      line-height: 16px;
      font-size: 11px;
    }
  }

  .page-meta {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--el-text-color-secondary);

    .meta-sep {
      color: var(--el-text-color-placeholder);
    }
  }
}

.page-actions {
  flex-shrink: 0;
  display: flex;
  gap: 0;
  opacity: 0;
  transition: opacity 0.2s;
}
</style>
