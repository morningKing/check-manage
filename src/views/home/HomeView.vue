<!--
 * 首页视图
 *
 * 职责：
 * - 动态渲染首页区块组件
 * - 从 systemConfigStore 获取可见区块配置
 * - 桌面态（>=768px）按管理员配置的网格坐标定位；移动态忽略坐标，单列纵向堆叠
 * - 支持快捷入口的批量导出功能
-->
<template>
  <div class="home-view" :class="{ 'home-view--grid': isDesktopLayout }">
    <template v-for="widget in visibleWidgets" :key="widget.id">
      <component
        :is="getWidgetComponent(widget.widgetType)"
        :content="widget.content"
        :title="widget.title"
        :widget-id="widget.id"
        class="widget-wrapper"
        :style="isDesktopLayout ? layoutToGridStyle(widget.layout) : undefined"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * HomeView 组件脚本
 *
 * 动态渲染首页区块，支持配置化管理
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useSystemConfigStore, usePageConfigStore } from '@/stores'
import { widgetComponentMap } from '@/components/home'
import { layoutToGridStyle } from '@/utils/homeGridLayout'
import type { WidgetType } from '@/types'

// ==================== Store ====================

const systemConfigStore = useSystemConfigStore()
const pageConfigStore = usePageConfigStore()

// ==================== 计算属性 ====================

/**
 * 可见区块列表
 */
const visibleWidgets = computed(() => systemConfigStore.visibleWidgets)

// ==================== 响应式布局 ====================

const DESKTOP_QUERY = '(min-width: 768px)'
let mediaQuery: MediaQueryList | null = null
const isDesktopLayout = ref(true)

function handleMediaChange(e: MediaQueryListEvent) {
  isDesktopLayout.value = e.matches
}

// ==================== 方法 ====================

/**
 * 根据区块类型获取对应组件
 */
function getWidgetComponent(type: WidgetType) {
  return widgetComponentMap[type]
}

// ==================== 生命周期 ====================

onMounted(async () => {
  await systemConfigStore.initialize()
  if (pageConfigStore.pageConfigs.length === 0) {
    await pageConfigStore.fetchPageConfigs()
  }

  if (typeof window !== 'undefined' && window.matchMedia) {
    mediaQuery = window.matchMedia(DESKTOP_QUERY)
    isDesktopLayout.value = mediaQuery.matches
    mediaQuery.addEventListener('change', handleMediaChange)
  }
})

onUnmounted(() => {
  mediaQuery?.removeEventListener('change', handleMediaChange)
})
</script>

<style scoped lang="scss">
.home-view {
  padding: 0;
}

.widget-wrapper {
  margin-bottom: 20px;

  &:last-child {
    margin-bottom: 0;
  }
}

.home-view--grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 16px;

  .widget-wrapper {
    margin-bottom: 0;
    min-width: 0;
  }
}
</style>
