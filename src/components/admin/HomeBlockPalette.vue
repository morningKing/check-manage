<template>
  <div class="home-block-palette">
    <div class="palette-title">添加区块</div>
    <div
      v-for="type in PALETTE_TYPES"
      :key="type"
      class="palette-card"
      draggable="true"
      @dragstart="handleDragStart($event, type)"
      @click="emit('add-at-bottom', type)"
    >
      <el-icon class="palette-icon">
        <component :is="PALETTE_ICONS[type]" />
      </el-icon>
      <span class="palette-label">{{ PALETTE_LABELS[type] }}</span>
    </div>
    <div class="palette-hint">拖到右侧网格可放到指定位置；点击默认加到底部</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 首页区块面板
 *
 * 展示现有 7 种可新增区块类型；点击卡片=加到网格底部（emit add-at-bottom），
 * 拖拽卡片到网格=由 HomeLayoutEditor 的 drop 处理接收并计算落点坐标。
 */
import type { Component } from 'vue'
import { Document, Files, EditPen, PieChart, List, Clock, Bell } from '@element-plus/icons-vue'
import type { CreatableWidgetType } from '@/types'

const emit = defineEmits<{
  'add-at-bottom': [type: CreatableWidgetType]
}>()

const PALETTE_TYPES: CreatableWidgetType[] = [
  'custom-markdown', 'data-card', 'quick-form', 'chart', 'todo', 'activity', 'announcement',
]

const PALETTE_LABELS: Record<CreatableWidgetType, string> = {
  'custom-markdown': 'Markdown 区块',
  'data-card': '数据卡片',
  'quick-form': '快速录入表单',
  chart: '图表区块',
  todo: '我的待办',
  activity: '最近动态',
  announcement: '公告',
}

const PALETTE_ICONS: Record<CreatableWidgetType, Component> = {
  'custom-markdown': Document,
  'data-card': Files,
  'quick-form': EditPen,
  chart: PieChart,
  todo: List,
  activity: Clock,
  announcement: Bell,
}

function handleDragStart(event: DragEvent, type: CreatableWidgetType) {
  if (!event.dataTransfer) return
  event.dataTransfer.setData('text/plain', type)
  event.dataTransfer.effectAllowed = 'copy'
}
</script>

<style scoped lang="scss">
.home-block-palette {
  width: 200px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.palette-title {
  font-weight: 500;
  margin-bottom: 4px;
}

.palette-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: var(--el-bg-color);
  cursor: grab;
  user-select: none;
  transition: box-shadow 0.2s;

  &:hover {
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  }

  &:active {
    cursor: grabbing;
  }
}

.palette-icon {
  color: var(--el-color-primary);
  flex-shrink: 0;
}

.palette-label {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.palette-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
  line-height: 1.4;
}
</style>
