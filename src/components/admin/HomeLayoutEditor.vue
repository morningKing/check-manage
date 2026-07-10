<template>
  <div class="home-layout-editor">
    <GridLayout
      v-model:layout="items"
      :col-num="12"
      :row-height="30"
      :margin="[12, 12]"
      :is-draggable="true"
      :is-resizable="true"
      :vertical-compact="true"
      :use-css-transforms="true"
      @layout-updated="handleLayoutUpdated"
    >
      <GridItem
        v-for="item in items"
        :key="item.i"
        :x="item.x"
        :y="item.y"
        :w="item.w"
        :h="item.h"
        :i="item.i"
        drag-allow-from=".grid-item-drag-handle"
      >
        <div v-if="widgetById(item.i)" class="grid-item-card">
          <div class="grid-item-drag-handle">
            <el-icon class="grid-item-rank"><Rank /></el-icon>
            <el-switch
              v-model="widgetById(item.i)!.enabled"
              size="small"
              @click.stop
              @change="emit('toggle', widgetById(item.i)!)"
            />
            <span class="grid-item-title">
              {{ widgetById(item.i)!.title || getDefaultTitle(widgetById(item.i)!.widgetType) }}
            </span>
            <el-tag size="small" :type="getTagType(widgetById(item.i)!.widgetType)">
              {{ getWidgetTypeLabel(widgetById(item.i)!.widgetType) }}
            </el-tag>
          </div>
          <div class="grid-item-actions">
            <el-button
              type="primary"
              link
              size="small"
              @click.stop="emit('edit', widgetById(item.i)!)"
            >
              <el-icon><Edit /></el-icon>
            </el-button>
            <el-button
              v-if="isCustomHomeWidget(widgetById(item.i)!)"
              type="danger"
              link
              size="small"
              @click.stop="emit('delete', widgetById(item.i)!)"
            >
              <el-icon><Delete /></el-icon>
            </el-button>
          </div>
        </div>
      </GridItem>
    </GridLayout>
  </div>
</template>

<script setup lang="ts">
/**
 * 首页网格布局编辑器
 *
 * 用 grid-layout-plus 渲染可拖拽/缩放的 12 列网格，每个区块用一张卡片展示
 * 标题/类型/启用开关/编辑删除按钮。拖拽或缩放结束后通过 layout-change 事件
 * 把最新坐标交给父组件（SystemSettings.vue）统一保存。
 */
import { computed, ref, watch } from 'vue'
import { GridLayout, GridItem } from 'grid-layout-plus'
import { Rank, Edit, Delete } from '@element-plus/icons-vue'
import { isCustomHomeWidget } from '@/utils/homeGridLayout'
import type { WidgetConfig, WidgetLayoutUpdateItem, WidgetType } from '@/types'

const props = defineProps<{
  widgets: WidgetConfig[]
}>()

const emit = defineEmits<{
  edit: [widget: WidgetConfig]
  delete: [widget: WidgetConfig]
  toggle: [widget: WidgetConfig]
  'layout-change': [items: WidgetLayoutUpdateItem[]]
}>()

// ==================== 网格状态 ====================

interface GridLayoutItem { i: string; x: number; y: number; w: number; h: number }

function toGridItems(widgets: WidgetConfig[]): GridLayoutItem[] {
  return widgets.map(w => ({ i: w.id, x: w.layout.x, y: w.layout.y, w: w.layout.w, h: w.layout.h }))
}

const items = ref<GridLayoutItem[]>(toGridItems(props.widgets))

// 只在区块集合变化（新增/删除）时重新同步坐标，避免覆盖用户正在拖拽的状态
watch(
  () => props.widgets.map(w => w.id).join(','),
  () => {
    items.value = toGridItems(props.widgets)
  }
)

const widgetMap = computed(() => new Map(props.widgets.map(w => [w.id, w])))
function widgetById(id: string | number): WidgetConfig | undefined {
  return widgetMap.value.get(String(id))
}

function handleLayoutUpdated() {
  emit('layout-change', items.value.map(it => ({ id: it.i, x: it.x, y: it.y, w: it.w, h: it.h })))
}

// ==================== 展示文案 ====================

const WIDGET_TYPE_LABELS: Record<string, string> = {
  welcome: '欢迎卡片',
  stats: '统计概览',
  'quick-links': '快捷入口',
  'system-info': '系统说明',
  'custom-markdown': 'Markdown',
  'data-card': '数据卡片',
  'quick-form': '快速录入',
  chart: '图表',
  todo: '我的待办',
  activity: '最近动态',
  announcement: '公告'
}

const WIDGET_DEFAULT_TITLES: Record<string, string> = { ...WIDGET_TYPE_LABELS }

function getWidgetTypeLabel(type: WidgetType): string {
  return WIDGET_TYPE_LABELS[type] || type
}

function getDefaultTitle(type: WidgetType): string {
  return WIDGET_DEFAULT_TITLES[type] || type
}

function getTagType(type: WidgetType): 'success' | 'warning' | 'info' | '' {
  if (type.startsWith('custom-')) return 'warning'
  if (type === 'welcome') return 'success'
  if (type === 'stats') return 'info'
  return ''
}
</script>

<style scoped lang="scss">
.home-layout-editor {
  :deep(.vgl-layout) {
    background: var(--el-fill-color-lighter);
    border-radius: 4px;
  }

  :deep(.vgl-item) {
    background: var(--el-bg-color);
  }
}

.grid-item-card {
  height: 100%;
  padding: 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: var(--el-bg-color);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  overflow: hidden;

  .grid-item-drag-handle {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: grab;
    overflow: hidden;

    &:active {
      cursor: grabbing;
    }
  }

  .grid-item-rank {
    color: var(--el-text-color-secondary);
    flex-shrink: 0;
  }

  .grid-item-title {
    flex: 1;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .grid-item-actions {
    display: flex;
    justify-content: flex-end;
    gap: 4px;
  }
}
</style>
